"""Testes de integração: rotas FastAPI, persistência e controle de acesso (OWASP).

Cada teste usa um banco SQLite em memória isolado, populado pela carga
inicial real (``app.seed``), injetado na aplicação via ``dependency_overrides``.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
import app.seed as seed_module
from app.database import get_db, init_db
from app.main import app
from app.models import (
    STATUS_DOMINADO,
    STATUS_EM_AQUISICAO,
    STATUS_NAO_INICIADO,
    ProgramaPEI,
    SessaoColeta,
    Usuario,
)
from app.schemas import SessaoColetaCreate
from app.security import COOKIE_SESSAO, criar_token_sessao, hash_senha
from app.seed import EMAIL_APLICADOR, EMAIL_SUPERVISORA, SENHA_PADRAO_DEMO, popular_banco
from app.services import ColetaService, MonitoramentoService

PEI_01, PEI_02 = 1, 2
_HASH_CACHE: dict[str, str] = {}


def _hash_em_cache(senha: str) -> str:
    """Bcrypt custo 12 real, calculado uma única vez por senha (acelera a suíte)."""
    if senha not in _HASH_CACHE:
        _HASH_CACHE[senha] = hash_senha(senha)
    return _HASH_CACHE[senha]


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def fabrica_sessao(monkeypatch) -> Iterator[sessionmaker]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_db(engine)
    fabrica = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed_module, "hash_senha", _hash_em_cache)
    with fabrica() as db:
        popular_banco(db)
    yield fabrica
    engine.dispose()


@pytest.fixture
def db(fabrica_sessao) -> Iterator[Session]:
    with fabrica_sessao() as sessao:
        yield sessao


@pytest.fixture
def client(fabrica_sessao) -> Iterator[TestClient]:
    def _get_db_teste():
        with fabrica_sessao() as sessao:
            yield sessao

    app.dependency_overrides[get_db] = _get_db_teste
    # Sem "with": o lifespan (que usa o banco padrão) não é disparado aqui.
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client_logado(client) -> TestClient:
    resposta = client.post(
        "/login",
        data={"email": EMAIL_APLICADOR, "senha": SENHA_PADRAO_DEMO},
        follow_redirects=False,
    )
    assert resposta.status_code == 303
    return client


@pytest.fixture
def auth_header(client) -> dict[str, str]:
    resposta = client.post(
        "/api/auth/login", json={"email": EMAIL_APLICADOR, "senha": SENHA_PADRAO_DEMO}
    )
    assert resposta.status_code == 200
    return {"Authorization": f"Bearer {resposta.json()['access_token']}"}


def _sessoes(db: Session, programa_id: int) -> list[SessaoColeta]:
    db.expire_all()
    return list(
        db.scalars(select(SessaoColeta).where(SessaoColeta.programa_id == programa_id))
    )


# --------------------------------------------------------------------------- #
# Infraestrutura
# --------------------------------------------------------------------------- #
def test_health(client):
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert resposta.json()["status"] == "ok"


def test_cabecalhos_de_seguranca(client):
    resposta = client.get("/")
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["X-Frame-Options"] == "DENY"


def test_lifespan_cria_tabelas_e_aplica_carga_inicial(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main_module, "init_db", lambda: init_db(engine))
    monkeypatch.setattr(main_module, "SessionLocal", fabrica)
    monkeypatch.setattr(seed_module, "hash_senha", _hash_em_cache)
    monkeypatch.setenv("SEED_ON_STARTUP", "1")

    with TestClient(app) as cliente:
        assert cliente.get("/health").status_code == 200
    with fabrica() as sessao:
        assert sessao.scalar(select(Usuario).where(Usuario.email == EMAIL_SUPERVISORA))
    engine.dispose()


def test_lifespan_respeita_seed_desativado(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    fabrica = sessionmaker(bind=engine)
    monkeypatch.setattr(main_module, "init_db", lambda: init_db(engine))
    monkeypatch.setattr(main_module, "SessionLocal", fabrica)
    monkeypatch.setenv("SEED_ON_STARTUP", "0")

    with TestClient(app):
        pass
    with fabrica() as sessao:
        assert sessao.scalars(select(Usuario)).all() == []
    engine.dispose()


# --------------------------------------------------------------------------- #
# Carga inicial e persistência
# --------------------------------------------------------------------------- #
def test_carga_inicial(db):
    supervisora = db.scalar(select(Usuario).where(Usuario.email == EMAIL_SUPERVISORA))
    assert supervisora.nome == "Evelin Lubas"
    assert supervisora.perfil == "supervisor"
    assert supervisora.senha_hash.startswith("$2b$12$")

    programas = db.scalars(select(ProgramaPEI).order_by(ProgramaPEI.id)).all()
    assert [p.codigo for p in programas] == ["PEI 01", "PEI 02"]
    assert {p.paciente_codigo for p in programas} == {"PAC-2026-04"}
    assert all(p.supervisor_id == supervisora.id for p in programas)
    assert programas[0].status == STATUS_EM_AQUISICAO
    assert programas[1].status == STATUS_NAO_INICIADO
    assert [s.percentual_independencia for s in _sessoes(db, PEI_01)] == [30.0, 60.0, 80.0]


def test_carga_inicial_e_idempotente(db):
    assert popular_banco(db) is False
    assert len(db.scalars(select(Usuario)).all()) == 2


def test_servico_registra_sessao_e_atualiza_status_automaticamente(db):
    aplicador = db.scalar(select(Usuario).where(Usuario.email == EMAIL_APLICADOR))
    servico = ColetaService(db)
    dados = SessaoColetaCreate(programa_id=PEI_02, tentativas=["I"] * 8 + ["AP", "E"])

    for esperado in [STATUS_EM_AQUISICAO, STATUS_EM_AQUISICAO, STATUS_DOMINADO]:
        sessao = servico.registrar_sessao(dados, aplicador)
        assert sessao.percentual_independencia == 80.0
        assert sessao.total_independentes == 8
        assert db.get(ProgramaPEI, PEI_02).status == esperado

    resultado = MonitoramentoService(db).resultado(PEI_02)
    assert resultado.total_sessoes == 3
    assert resultado.sessoes_consecutivas_no_criterio == 3


def test_servico_desfaz_transacao_em_caso_de_falha(db, monkeypatch):
    aplicador = db.scalar(select(Usuario).where(Usuario.email == EMAIL_APLICADOR))
    servico = ColetaService(db)

    def _falhar(_programa):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(servico, "_atualizar_status", _falhar)
    with pytest.raises(RuntimeError):
        servico.registrar_sessao(SessaoColetaCreate(programa_id=PEI_02, tentativas=["I"]), aplicador)
    assert _sessoes(db, PEI_02) == []


# --------------------------------------------------------------------------- #
# Tela 1 — menu e autenticação
# --------------------------------------------------------------------------- #
def test_menu_anonimo_exibe_formulario_de_login(client):
    resposta = client.get("/")
    assert resposta.status_code == 200
    assert 'action="/login"' in resposta.text
    assert "PEI 01" not in resposta.text


@pytest.mark.parametrize(
    ("email", "senha"),
    [
        (EMAIL_APLICADOR, "senha-errada"),
        ("ninguem@abrace.local", SENHA_PADRAO_DEMO),
        ("email-invalido", SENHA_PADRAO_DEMO),
        ("", ""),
    ],
)
def test_login_formulario_invalido(client, email, senha):
    resposta = client.post("/login", data={"email": email, "senha": senha}, follow_redirects=False)
    assert resposta.status_code == 401
    assert "E-mail ou senha inválidos." in resposta.text
    assert COOKIE_SESSAO not in resposta.cookies


def test_login_define_cookie_seguro_e_menu_lista_programas(client):
    resposta = client.post(
        "/login",
        data={"email": EMAIL_SUPERVISORA, "senha": SENHA_PADRAO_DEMO},
        follow_redirects=False,
    )
    assert resposta.status_code == 303
    cookie = resposta.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie

    menu = client.get("/")
    assert "Evelin Lubas" in menu.text
    assert "PEI 01" in menu.text and "PEI 02" in menu.text
    assert "PAC-2026-04" in menu.text
    assert "Em aquisição" in menu.text and "Não iniciado" in menu.text


def test_logout_remove_sessao(client_logado):
    resposta = client_logado.post("/logout", follow_redirects=False)
    assert resposta.status_code == 303
    client_logado.cookies.clear()
    assert 'action="/login"' in client_logado.get("/").text


# --------------------------------------------------------------------------- #
# Controle de acesso (OWASP A01/A07)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("metodo", "rota"),
    [("get", "/coleta/1"), ("get", "/resultados/1"), ("post", "/coleta/1")],
)
def test_telas_protegidas_redirecionam_sem_sessao(client, metodo, rota):
    resposta = getattr(client, metodo)(rota, follow_redirects=False)
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/"


def test_cookie_adulterado_nao_autentica(client):
    token = criar_token_sessao(1)
    client.cookies.set(COOKIE_SESSAO, token[:-3] + "abc")
    assert client.get("/coleta/1", follow_redirects=False).status_code == 303


@pytest.mark.parametrize(
    ("metodo", "rota", "corpo"),
    [
        ("get", "/api/programas", None),
        ("get", "/api/programas/1/resultados", None),
        ("post", "/api/sessoes", {"programa_id": 1, "tentativas": ["I"]}),
    ],
)
def test_api_exige_autenticacao(client, metodo, rota, corpo):
    kwargs = {"json": corpo} if corpo else {}
    resposta = getattr(client, metodo)(rota, **kwargs)
    assert resposta.status_code == 401
    assert resposta.headers["www-authenticate"] == "Bearer"


def test_api_recusa_token_invalido(client):
    resposta = client.get("/api/programas", headers={"Authorization": "Bearer token.falso"})
    assert resposta.status_code == 401


def test_usuario_desativado_perde_acesso(client, auth_header, db):
    usuario = db.scalar(select(Usuario).where(Usuario.email == EMAIL_APLICADOR))
    usuario.ativo = False
    db.commit()

    assert client.get("/api/programas", headers=auth_header).status_code == 401
    resposta = client.post(
        "/api/auth/login", json={"email": EMAIL_APLICADOR, "senha": SENHA_PADRAO_DEMO}
    )
    assert resposta.status_code == 401


def test_sessao_nao_grava_dados_sem_autenticacao(client, db):
    client.post("/coleta/2", data={"tentativas": "I,I"}, follow_redirects=False)
    client.post("/api/sessoes", json={"programa_id": 2, "tentativas": ["I"]})
    assert _sessoes(db, PEI_02) == []


# --------------------------------------------------------------------------- #
# Tela 2 — coleta
# --------------------------------------------------------------------------- #
def test_tela2_exibe_botoes_com_tripla_codificacao(client_logado):
    resposta = client_logado.get("/coleta/1")
    assert resposta.status_code == 200
    for rotulo, simbolo in [
        ("Independente", "✔"),
        ("Ajuda Parcial", "◐"),
        ("Ajuda Total", "●"),
        ("Erro", "✖"),
    ]:
        assert rotulo in resposta.text and simbolo in resposta.text
    assert "Finalizar e salvar sessão" in resposta.text
    assert 'aria-describedby="legenda-codigos"' in resposta.text
    assert "min-h-[52px]" in resposta.text
    assert "focus-visible:ring" in resposta.text


def test_coleta_via_formulario_persiste_e_redireciona(client_logado, db):
    resposta = client_logado.post(
        "/coleta/2",
        data={"tentativas": "I,I,I,AP,E", "observacoes": "Boa atenção compartilhada"},
        follow_redirects=False,
    )
    assert resposta.status_code == 303
    assert resposta.headers["location"] == "/resultados/2?registrada=1"

    [sessao] = _sessoes(db, PEI_02)
    assert sessao.tentativas == "I,I,I,AP,E"
    assert sessao.percentual_independencia == 60.0
    assert sessao.aplicador.email == EMAIL_APLICADOR
    assert db.get(ProgramaPEI, PEI_02).status == STATUS_EM_AQUISICAO

    tela3 = client_logado.get(resposta.headers["location"])
    assert "Sessão registrada com sucesso." in tela3.text
    assert "60.0%" in tela3.text


@pytest.mark.parametrize(
    ("tentativas", "mensagem"),
    [
        ("", "Registre ao menos uma tentativa"),
        ("I,X", "use apenas I, AP, AT ou E"),
        (",".join(["I"] * 51), "máximo é de 50 tentativas"),
    ],
)
def test_coleta_via_formulario_invalida(client_logado, db, tentativas, mensagem):
    resposta = client_logado.post("/coleta/2", data={"tentativas": tentativas})
    assert resposta.status_code == 422
    assert mensagem in resposta.text
    assert _sessoes(db, PEI_02) == []


def test_observacoes_longas_retornam_mensagem(client_logado):
    resposta = client_logado.post(
        "/coleta/2", data={"tentativas": "I", "observacoes": "a" * 501}
    )
    assert resposta.status_code == 422
    assert "no máximo 500 caracteres" in resposta.text


def test_coleta_sanitiza_xss_nas_observacoes(client_logado, db):
    carga = "<script>alert('xss')</script><img src=x onerror=alert(1)>Sessão ok"
    client_logado.post("/coleta/2", data={"tentativas": "I", "observacoes": carga})

    [sessao] = _sessoes(db, PEI_02)
    assert sessao.observacoes == "Sessão ok"
    tela3 = client_logado.get("/resultados/2")
    assert "alert(" not in tela3.text
    assert "onerror" not in tela3.text


@pytest.mark.parametrize(
    ("metodo", "rota"),
    [("get", "/coleta/999"), ("post", "/coleta/999"), ("get", "/resultados/999")],
)
def test_programa_inexistente_retorna_404(client_logado, metodo, rota):
    kwargs = {"data": {"tentativas": "I"}} if metodo == "post" else {}
    resposta = getattr(client_logado, metodo)(rota, **kwargs)
    assert resposta.status_code == 404
    assert "Programa PEI não encontrado." in resposta.text


# --------------------------------------------------------------------------- #
# Tela 3 — resultados
# --------------------------------------------------------------------------- #
def test_tela3_exibe_indicadores_do_historico(client_logado):
    resposta = client_logado.get("/resultados/1")
    assert resposta.status_code == 200
    assert "Em aquisição" in resposta.text
    assert "56.7%" in resposta.text  # média de 30, 60 e 80
    assert "1 de 3 atingida" in resposta.text
    assert "Sessão 3" in resposta.text


def test_tela3_sem_sessoes(client_logado):
    resposta = client_logado.get("/resultados/2")
    assert "Nenhuma sessão registrada" in resposta.text
    assert "Não iniciado" in resposta.text


def test_fluxo_completo_ate_o_dominio(client_logado):
    for _ in range(2):
        client_logado.post("/coleta/1", data={"tentativas": "I,I,I,I,E"})
    resposta = client_logado.get("/resultados/1")
    assert "Dominado" in resposta.text
    assert "3 de 3 atingida" in resposta.text


# --------------------------------------------------------------------------- #
# API REST
# --------------------------------------------------------------------------- #
def test_api_login_retorna_token(client):
    resposta = client.post(
        "/api/auth/login", json={"email": EMAIL_SUPERVISORA, "senha": SENHA_PADRAO_DEMO}
    )
    assert resposta.status_code == 200
    assert resposta.json()["token_type"] == "bearer"


def test_api_login_invalido(client):
    resposta = client.post("/api/auth/login", json={"email": EMAIL_SUPERVISORA, "senha": "x"})
    assert resposta.status_code == 401
    assert resposta.json()["detail"] == "E-mail ou senha inválidos."


def test_api_lista_programas(client, auth_header):
    resposta = client.get("/api/programas", headers=auth_header)
    assert resposta.status_code == 200
    dados = resposta.json()
    assert [p["codigo"] for p in dados] == ["PEI 01", "PEI 02"]
    assert all("nome" not in p for p in dados)  # sem dados pessoais (LGPD)


def test_api_registra_sessao(client, auth_header):
    resposta = client.post(
        "/api/sessoes",
        json={"programa_id": 2, "tentativas": ["I", "AP", "AT", "E"], "observacoes": "<b>ok</b>"},
        headers=auth_header,
    )
    assert resposta.status_code == 201
    corpo = resposta.json()
    assert corpo["tentativas"] == ["I", "AP", "AT", "E"]
    assert corpo["percentual_independencia"] == 25.0
    assert corpo["observacoes"] == "ok"


@pytest.mark.parametrize(
    ("corpo", "status"),
    [
        ({"programa_id": 2, "tentativas": ["Z"]}, 422),
        ({"programa_id": 2, "tentativas": []}, 422),
        ({"programa_id": 2}, 422),
        ({"programa_id": 999, "tentativas": ["I"]}, 404),
    ],
)
def test_api_registra_sessao_invalida(client, auth_header, corpo, status):
    assert client.post("/api/sessoes", json=corpo, headers=auth_header).status_code == status


def test_api_resultados(client, auth_header):
    resposta = client.get("/api/programas/1/resultados", headers=auth_header)
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["programa"]["status"] == STATUS_EM_AQUISICAO
    assert corpo["total_sessoes"] == 3
    assert corpo["media_independencia"] == 56.7
    assert corpo["ultimo_percentual"] == 80.0
    assert corpo["sessoes_consecutivas_exigidas"] == 3

    assert client.get("/api/programas/999/resultados", headers=auth_header).status_code == 404


# --------------------------------------------------------------------------- #
# Acessibilidade — modo de alto contraste em todas as telas
# --------------------------------------------------------------------------- #
def _verificar_alto_contraste(html: str) -> None:
    assert 'id="botao-alto-contraste"' in html
    assert 'aria-pressed="false"' in html
    assert "html.alto-contraste" in html  # CSS do modo
    assert "prefers-contrast: more" in html  # respeita a preferência do sistema
    assert html.count('id="botao-alto-contraste"') == 1


def test_alto_contraste_na_tela_de_login(client):
    _verificar_alto_contraste(client.get("/").text)


@pytest.mark.parametrize("rota", ["/", "/coleta/1", "/resultados/1"])
def test_alto_contraste_em_todas_as_telas(client_logado, rota):
    resposta = client_logado.get(rota)
    assert resposta.status_code == 200
    _verificar_alto_contraste(resposta.text)


def test_alto_contraste_preserva_tripla_codificacao(client_logado):
    coleta = client_logado.get("/coleta/1").text
    for classe in ["hc-verde", "hc-amarelo", "hc-laranja", "hc-vermelho"]:
        assert classe in coleta
    resultados = client_logado.get("/resultados/1").text
    assert "hc-barra-ok" in resultados and "hc-barra-abaixo" in resultados
    assert "hc-linha-criterio" in resultados
