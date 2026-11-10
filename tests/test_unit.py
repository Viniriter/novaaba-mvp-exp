"""Testes unitários: funções puras de cálculo ABA, validação Pydantic e Bcrypt.

Nenhum teste deste módulo acessa banco de dados ou rede.
"""

import pytest
from pydantic import ValidationError

from app import database
from app.models import (
    STATUS_DOMINADO,
    STATUS_EM_AQUISICAO,
    STATUS_NAO_INICIADO,
    ProgramaPEI,
    SessaoColeta,
    Usuario,
)
from app.schemas import (
    MAX_TENTATIVAS_POR_SESSAO,
    LoginRequest,
    SessaoColetaCreate,
    SessaoColetaOut,
    sanitizar_texto,
)
from app.security import (
    BCRYPT_ROUNDS,
    criar_token_sessao,
    eh_hash_bcrypt,
    hash_senha,
    ler_token_sessao,
    verificar_senha,
)
from app.services import (
    calcular_media,
    calcular_percentual_independencia,
    contar_sessoes_consecutivas_no_criterio,
    determinar_status,
    verificar_criterio_dominio,
)

# =========================================================================== #
# Cálculos ABA (funções puras da camada de serviço)
# =========================================================================== #
class TestPercentualIndependencia:
    @pytest.mark.parametrize(
        ("tentativas", "esperado"),
        [
            (["I", "I", "I", "I"], 100.0),
            (["E", "AP", "AT"], 0.0),
            (["I", "AP", "AT", "E"], 25.0),
            (["I", "I", "I", "I", "I", "I", "I", "I", "AP", "E"], 80.0),
            (["I", "E", "E"], 33.3),
            (["I", "I", "E"], 66.7),
        ],
    )
    def test_calcula_percentual_de_respostas_independentes(self, tentativas, esperado):
        assert calcular_percentual_independencia(tentativas) == esperado

    def test_ajudas_nao_contam_como_independencia(self):
        assert calcular_percentual_independencia(["AP", "AP", "AT", "AT"]) == 0.0

    def test_sessao_sem_tentativas_retorna_zero(self):
        assert calcular_percentual_independencia([]) == 0.0

    def test_rejeita_codigo_fora_do_dominio(self):
        with pytest.raises(ValueError, match="Tentativas inválidas"):
            calcular_percentual_independencia(["I", "X"])


class TestCriterioDominio:
    def test_conta_sessoes_consecutivas_a_partir_da_mais_recente(self):
        assert contar_sessoes_consecutivas_no_criterio([90, 50, 80, 85]) == 2

    def test_sessao_abaixo_do_criterio_interrompe_a_sequencia(self):
        assert contar_sessoes_consecutivas_no_criterio([90, 90, 79.9]) == 0

    def test_exatamente_80_por_cento_atende_o_criterio(self):
        assert verificar_criterio_dominio([80.0, 80.0, 80.0]) is True

    def test_menos_de_tres_sessoes_nao_atinge_dominio(self):
        assert verificar_criterio_dominio([100.0, 100.0]) is False

    def test_criterio_personalizado(self):
        assert verificar_criterio_dominio([70, 75, 72], criterio=70) is True
        assert verificar_criterio_dominio([90, 90], sessoes_consecutivas=2) is True

    @pytest.mark.parametrize(
        ("percentuais", "status"),
        [
            ([], STATUS_NAO_INICIADO),
            ([30.0], STATUS_EM_AQUISICAO),
            ([60.0, 80.0, 90.0], STATUS_EM_AQUISICAO),
            ([60.0, 80.0, 90.0, 85.0], STATUS_DOMINADO),
            ([90.0, 90.0, 90.0, 50.0], STATUS_EM_AQUISICAO),
        ],
    )
    def test_determina_status_do_pei(self, percentuais, status):
        assert determinar_status(percentuais) == status

    def test_media(self):
        assert calcular_media([30.0, 60.0, 80.0]) == 56.7
        assert calcular_media([]) == 0.0


# =========================================================================== #
# Validação Pydantic (Camada de Controle)
# =========================================================================== #
class TestSessaoColetaCreate:
    def test_aceita_os_quatro_codigos_validos(self):
        dados = SessaoColetaCreate(programa_id=1, tentativas=["I", "AP", "AT", "E"])
        assert dados.tentativas == ["I", "AP", "AT", "E"]

    def test_normaliza_texto_separado_por_virgula_e_minusculas(self):
        dados = SessaoColetaCreate(programa_id=1, tentativas=" i, ap ,AT,e,")
        assert dados.tentativas == ["I", "AP", "AT", "E"]

    @pytest.mark.parametrize("invalida", ["X", "A", "IA", "1", "", "<script>"])
    def test_rejeita_tentativa_fora_do_dominio(self, invalida):
        with pytest.raises(ValidationError):
            SessaoColetaCreate(programa_id=1, tentativas=["I", invalida])

    @pytest.mark.parametrize("valor", [[1, 2], None, 7])
    def test_rejeita_tipo_nao_textual(self, valor):
        with pytest.raises(ValidationError):
            SessaoColetaCreate(programa_id=1, tentativas=valor)

    def test_rejeita_sessao_sem_tentativas(self):
        with pytest.raises(ValidationError):
            SessaoColetaCreate(programa_id=1, tentativas=[])

    def test_rejeita_excesso_de_tentativas(self):
        with pytest.raises(ValidationError):
            SessaoColetaCreate(programa_id=1, tentativas=["I"] * (MAX_TENTATIVAS_POR_SESSAO + 1))

    def test_rejeita_programa_invalido(self):
        with pytest.raises(ValidationError):
            SessaoColetaCreate(programa_id=0, tentativas=["I"])

    def test_rejeita_observacoes_longas(self):
        with pytest.raises(ValidationError):
            SessaoColetaCreate(programa_id=1, tentativas=["I"], observacoes="a" * 501)


class TestSanitizacaoXSS:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [
            ("<script>alert('xss')</script>Sessão tranquila", "Sessão tranquila"),
            ("<img src=x onerror=alert(1)>Atenção", "Atenção"),
            ("<b>negrito</b> e <i>itálico</i>", "negrito e itálico"),
            ('<a href="javascript:alert(1)">link</a>', "link"),
            ("javascript:alert(1)", "alert(1)"),
            ("<iframe src='//mal.example'></iframe>ok", "ok"),
            ("<style>body{display:none}</style>ok", "ok"),
            ("<!-- comentário -->ok", "ok"),
            ("< / script >ok", "ok"),
            ("texto\x00com\x07controle", "textocomcontrole"),
            ("criança < 3 tentativas > meta", "criança  3 tentativas  meta"),
            ("   ", None),
            ("<script></script>", None),
        ],
    )
    def test_sanitiza_observacoes(self, entrada, esperado):
        assert sanitizar_texto(entrada) == esperado
        if esperado is not None:
            dados = SessaoColetaCreate(programa_id=1, tentativas=["I"], observacoes=entrada)
            assert dados.observacoes == esperado

    def test_preserva_quebras_de_linha_e_acentos(self):
        assert sanitizar_texto("Linha 1\nAção concluída") == "Linha 1\nAção concluída"

    def test_none_permanece_none(self):
        assert sanitizar_texto(None) is None


class TestOutrosSchemas:
    def test_login_normaliza_email(self):
        assert LoginRequest(email="  Evelin@Abrace.LOCAL ", senha="x").email == "evelin@abrace.local"

    @pytest.mark.parametrize("email", ["sem-arroba", "a b@c.d", "@"])
    def test_login_rejeita_email_invalido(self, email):
        with pytest.raises(ValidationError):
            LoginRequest(email=email, senha="x")

    def test_saida_converte_sequencia_armazenada_em_lista(self):
        sessao = SessaoColeta(
            id=1,
            programa_id=1,
            aplicador_id=1,
            data_hora=__import__("datetime").datetime(2026, 1, 1),
            tentativas="I,AP,E",
            total_tentativas=3,
            total_independentes=1,
            percentual_independencia=33.3,
        )
        assert sessao.lista_tentativas == ["I", "AP", "E"]
        assert SessaoColetaOut.model_validate(sessao).tentativas == ["I", "AP", "E"]

        dados = SessaoColetaOut.model_validate(sessao).model_dump()
        assert SessaoColetaOut(**dados).tentativas == ["I", "AP", "E"]


# =========================================================================== #
# Segurança: Bcrypt e tokens de sessão
# =========================================================================== #
class TestBcrypt:
    def test_hash_usa_bcrypt_com_custo_12(self):
        senha_hash = hash_senha("Abrace@2026")
        assert BCRYPT_ROUNDS == 12
        assert senha_hash.startswith("$2b$12$")
        assert eh_hash_bcrypt(senha_hash)

    def test_hash_nunca_contem_a_senha_em_texto_limpo(self):
        assert "Abrace@2026" not in hash_senha("Abrace@2026")

    def test_salt_aleatorio_gera_hashes_diferentes(self):
        assert hash_senha("mesma-senha") != hash_senha("mesma-senha")

    def test_verifica_senha_correta_e_incorreta(self):
        senha_hash = hash_senha("correta")
        assert verificar_senha("correta", senha_hash) is True
        assert verificar_senha("errada", senha_hash) is False
        assert verificar_senha("", senha_hash) is False

    @pytest.mark.parametrize(
        "hash_fraco",
        [
            "correta",  # texto limpo
            "e7d3e769f3f593dadcb8634cc5b09fc9",  # MD5("correta")
            "1f1d4a1a2b7e4c1a9d0c6a3f9a0b8c7d6e5f4a3b",  # formato SHA-1
            "$2b$12$curto",
            "",
        ],
    )
    def test_recusa_hashes_que_nao_sao_bcrypt(self, hash_fraco):
        assert eh_hash_bcrypt(hash_fraco) is False
        assert verificar_senha("correta", hash_fraco) is False

    def test_hash_bcrypt_corrompido_retorna_falso(self):
        corrompido = "$2b$12$" + "!" * 53
        assert verificar_senha("correta", corrompido) is False

    def test_senha_vazia_nao_gera_hash(self):
        with pytest.raises(ValueError):
            hash_senha("")

    def test_senha_longa_multibyte(self):
        senha = "ç" * 100  # 200 bytes em UTF-8
        assert verificar_senha(senha, hash_senha(senha)) is True


class TestTokenSessao:
    def test_token_valido_retorna_usuario(self):
        assert ler_token_sessao(criar_token_sessao(42)) == 42

    def test_token_adulterado_e_recusado(self):
        token = criar_token_sessao(42)
        payload, assinatura = token.split(".")
        outro_payload = criar_token_sessao(1).split(".")[0]
        assert ler_token_sessao(f"{outro_payload}.{assinatura}") is None
        assert ler_token_sessao(f"{payload}.{assinatura[:-2]}xx") is None

    def test_token_expirado_e_recusado(self):
        token = criar_token_sessao(42, validade_segundos=60, agora=1_000)
        assert ler_token_sessao(token, agora=1_059) == 42
        assert ler_token_sessao(token, agora=1_061) is None

    def test_token_assinado_com_outra_chave_e_recusado(self):
        assert ler_token_sessao(criar_token_sessao(42, chave="outra")) is None

    @pytest.mark.parametrize("token", [None, "", "semponto", "a.b.c", "x.y"])
    def test_tokens_malformados(self, token):
        assert ler_token_sessao(token) is None

    def test_payload_malformado_mesmo_que_assinado(self):
        from app.security import _assinar, _b64

        payload = _b64(b"nao-numerico")
        assert ler_token_sessao(f"{payload}.{_assinar(payload, 'k')}", chave="k") is None


# =========================================================================== #
# Regras dos modelos (LGPD) e configuração do banco
# =========================================================================== #
class TestModelos:
    @pytest.mark.parametrize("nome_real", ["João da Silva", "PAC-26-04", "123.456.789-00", ""])
    def test_paciente_somente_por_codigo_anonimizado(self, nome_real):
        with pytest.raises(ValueError, match="LGPD"):
            ProgramaPEI(paciente_codigo=nome_real)

    def test_codigo_anonimizado_e_normalizado(self):
        assert ProgramaPEI(paciente_codigo=" pac-2026-04 ").paciente_codigo == "PAC-2026-04"

    def test_status_invalido(self):
        with pytest.raises(ValueError):
            ProgramaPEI(status="CONCLUIDO")

    def test_perfil_invalido(self):
        with pytest.raises(ValueError):
            Usuario(perfil="admin")

    def test_email_normalizado(self):
        assert Usuario(email=" Evelin@Abrace.Local ").email == "evelin@abrace.local"


class TestDatabaseConfig:
    def test_fallback_sqlite(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert database.resolver_database_url() == "sqlite:///./novaaba.db"

    def test_postgres_legado_e_normalizado(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://u:s@h:5432/db")
        assert database.resolver_database_url() == "postgresql://u:s@h:5432/db"

    def test_url_personalizada(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:s@h/db")
        assert database.resolver_database_url() == "postgresql+psycopg://u:s@h/db"

    def test_get_db_fornece_e_fecha_sessao(self):
        gerador = database.get_db()
        sessao = next(gerador)
        assert sessao is not None
        gerador.close()
