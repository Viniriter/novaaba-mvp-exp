"""Ponto de entrada FastAPI: rotas HTML (Telas 1, 2 e 3) e API REST.

Camada de Controle: recebe requisições, valida com Pydantic, aplica controle
de acesso e delega as regras de negócio à camada de serviço.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import __version__
from app.database import SessionLocal, get_db, init_db
from app.models import PERFIL_SUPERVISOR, Usuario
from app.schemas import (
    MAX_OBSERVACOES,
    MAX_TENTATIVAS_POR_SESSAO,
    LoginRequest,
    ProgramaPEIOut,
    ResultadoPrograma,
    SessaoColetaCreate,
    SessaoColetaOut,
    TokenOut,
)
from app.security import (
    COOKIE_SESSAO,
    TOKEN_VALIDADE_SEGUNDOS,
    criar_token_sessao,
    ler_token_sessao,
)
from app.seed import popular_banco
from app.services import (
    AutenticacaoService,
    ColetaService,
    CredenciaisInvalidas,
    MonitoramentoService,
    RecursoNaoEncontrado,
    ServiceError,
)

# --------------------------------------------------------------------------- #
# Apresentação: templates e codificação visual tripla (cor + símbolo + texto)
# --------------------------------------------------------------------------- #
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

TENTATIVA_UI = {
    "I": {"rotulo": "Independente", "simbolo": "✔", "classes": "hc-verde bg-emerald-700 text-white"},
    "AP": {"rotulo": "Ajuda Parcial", "simbolo": "◐", "classes": "hc-amarelo bg-amber-300 text-slate-900"},
    "AT": {"rotulo": "Ajuda Total", "simbolo": "●", "classes": "hc-laranja bg-orange-700 text-white"},
    "E": {"rotulo": "Erro", "simbolo": "✖", "classes": "hc-vermelho bg-red-700 text-white"},
}
STATUS_UI = {
    "NAO_INICIADO": {
        "rotulo": "Não iniciado",
        "simbolo": "○",
        "classes": "hc-cinza bg-slate-100 text-slate-800 border-slate-600",
    },
    "EM_AQUISICAO": {
        "rotulo": "Em aquisição",
        "simbolo": "▲",
        "classes": "hc-amarelo bg-amber-100 text-amber-900 border-amber-700",
    },
    "DOMINADO": {
        "rotulo": "Dominado",
        "simbolo": "✔",
        "classes": "hc-verde bg-emerald-100 text-emerald-900 border-emerald-700",
    },
}


def _data_br(valor: datetime | None) -> str:
    return valor.strftime("%d/%m/%Y %H:%M") if valor else "—"


templates.env.filters["data_br"] = _data_br
templates.env.globals.update(TENTATIVA_UI=TENTATIVA_UI, STATUS_UI=STATUS_UI)

MENSAGENS_VALIDACAO = {
    ("tentativas", "too_short"): "Registre ao menos uma tentativa antes de salvar.",
    ("tentativas", "too_long"): f"O máximo é de {MAX_TENTATIVAS_POR_SESSAO} tentativas por sessão.",
    ("tentativas", "literal_error"): "Tentativa inválida: use apenas I, AP, AT ou E.",
    ("observacoes", "string_too_long"): f"As observações devem ter no máximo {MAX_OBSERVACOES} caracteres.",
}


def _mensagens_amigaveis(erro: ValidationError) -> list[str]:
    mensagens: list[str] = []
    for item in erro.errors():
        campo = str(item["loc"][0]) if item["loc"] else ""
        mensagem = MENSAGENS_VALIDACAO.get((campo, item["type"]), "Dados inválidos.")
        if mensagem not in mensagens:
            mensagens.append(mensagem)
    return mensagens


# --------------------------------------------------------------------------- #
# Aplicação
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    init_db()
    if os.getenv("SEED_ON_STARTUP", "1") != "0":
        with SessionLocal() as db:
            popular_banco(db)
    yield


app = FastAPI(
    title="NovaABA - Coleta e Monitoramento de PEI",
    description="MVP da Clínica Abrace para coleta de tentativas ABA e monitoramento do PEI.",
    version=__version__,
    lifespan=lifespan,
)


@app.middleware("http")
async def cabecalhos_seguranca(request: Request, call_next) -> Response:
    resposta = await call_next(request)
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "same-origin"
    return resposta


@app.exception_handler(ServiceError)
async def tratar_erro_servico(_request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.mensagem})


# --------------------------------------------------------------------------- #
# Controle de acesso
# --------------------------------------------------------------------------- #
DbSession = Annotated[Session, Depends(get_db)]


def obter_usuario_opcional(request: Request, db: DbSession) -> Usuario | None:
    """Identifica o usuário pelo cookie de sessão ou pelo cabeçalho Bearer."""
    token = request.cookies.get(COOKIE_SESSAO)
    autorizacao = request.headers.get("Authorization", "")
    if autorizacao.lower().startswith("bearer "):
        token = autorizacao[7:].strip()
    return AutenticacaoService(db).obter_usuario_ativo(ler_token_sessao(token))


UsuarioOpcional = Annotated[Usuario | None, Depends(obter_usuario_opcional)]


def exigir_usuario(usuario: UsuarioOpcional) -> Usuario:
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return usuario


UsuarioAutenticado = Annotated[Usuario, Depends(exigir_usuario)]


def _redirecionar_login() -> RedirectResponse:
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


def _contexto_usuario(usuario: Usuario) -> dict:
    perfil = "Supervisão" if usuario.perfil == PERFIL_SUPERVISOR else "Aplicação"
    return {"usuario": usuario, "perfil_rotulo": perfil}


def _render_menu(
    request: Request, db: Session, usuario: Usuario | None, erro: str | None, codigo: int = 200
) -> HTMLResponse:
    programas = MonitoramentoService(db).listar_programas() if usuario else []
    contexto = {"programas": programas, "erro": erro, "usuario": usuario}
    if usuario:
        contexto.update(_contexto_usuario(usuario))
    return templates.TemplateResponse(request, "tela1_menu.html", contexto, status_code=codigo)


# --------------------------------------------------------------------------- #
# Rotas HTML
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def tela1_menu(request: Request, db: DbSession, usuario: UsuarioOpcional) -> HTMLResponse:
    return _render_menu(request, db, usuario, erro=None)


@app.post("/login", include_in_schema=False)
def login_formulario(
    request: Request,
    db: DbSession,
    email: Annotated[str, Form()] = "",
    senha: Annotated[str, Form()] = "",
) -> Response:
    try:
        credenciais = LoginRequest(email=email, senha=senha)
        usuario = AutenticacaoService(db).autenticar(credenciais.email, credenciais.senha)
    except (ValidationError, CredenciaisInvalidas):
        return _render_menu(request, db, None, "E-mail ou senha inválidos.", codigo=401)

    resposta = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resposta.set_cookie(
        COOKIE_SESSAO,
        criar_token_sessao(usuario.id),
        max_age=TOKEN_VALIDADE_SEGUNDOS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return resposta


@app.post("/logout", include_in_schema=False)
def logout() -> RedirectResponse:
    resposta = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resposta.delete_cookie(COOKIE_SESSAO)
    return resposta


def _render_coleta(
    request: Request,
    usuario: Usuario,
    programa,
    erros: list[str] | None = None,
    tentativas: list[str] | None = None,
    observacoes: str = "",
    codigo: int = 200,
) -> HTMLResponse:
    contexto = {
        "programa": programa,
        "erros": erros or [],
        "tentativas_previas": tentativas or [],
        "observacoes": observacoes,
        "max_tentativas": MAX_TENTATIVAS_POR_SESSAO,
        "max_observacoes": MAX_OBSERVACOES,
        **_contexto_usuario(usuario),
    }
    return templates.TemplateResponse(request, "tela2_coleta.html", contexto, status_code=codigo)


@app.get("/coleta/{programa_id}", response_class=HTMLResponse, include_in_schema=False)
def tela2_coleta(
    programa_id: int, request: Request, db: DbSession, usuario: UsuarioOpcional
) -> Response:
    if usuario is None:
        return _redirecionar_login()
    try:
        programa = MonitoramentoService(db).obter_programa(programa_id)
    except RecursoNaoEncontrado as exc:
        return _render_menu(request, db, usuario, exc.mensagem, codigo=404)
    return _render_coleta(request, usuario, programa)


@app.post("/coleta/{programa_id}", include_in_schema=False)
def salvar_coleta_formulario(
    programa_id: int,
    request: Request,
    db: DbSession,
    usuario: UsuarioOpcional,
    tentativas: Annotated[str, Form()] = "",
    observacoes: Annotated[str, Form()] = "",
) -> Response:
    if usuario is None:
        return _redirecionar_login()
    monitoramento = MonitoramentoService(db)
    try:
        programa = monitoramento.obter_programa(programa_id)
    except RecursoNaoEncontrado as exc:
        return _render_menu(request, db, usuario, exc.mensagem, codigo=404)

    try:
        dados = SessaoColetaCreate(
            programa_id=programa_id, tentativas=tentativas, observacoes=observacoes or None
        )
    except ValidationError as exc:
        previas = [t.strip().upper() for t in tentativas.split(",") if t.strip()]
        return _render_coleta(
            request,
            usuario,
            programa,
            erros=_mensagens_amigaveis(exc),
            tentativas=[t for t in previas if t in TENTATIVA_UI],
            observacoes=observacoes,
            codigo=422,
        )

    ColetaService(db).registrar_sessao(dados, usuario)
    return RedirectResponse(
        f"/resultados/{programa_id}?registrada=1", status_code=status.HTTP_303_SEE_OTHER
    )


@app.get("/resultados/{programa_id}", response_class=HTMLResponse, include_in_schema=False)
def tela3_resultados(
    programa_id: int,
    request: Request,
    db: DbSession,
    usuario: UsuarioOpcional,
    registrada: int = 0,
) -> Response:
    if usuario is None:
        return _redirecionar_login()
    try:
        resultado = MonitoramentoService(db).resultado(programa_id)
    except RecursoNaoEncontrado as exc:
        return _render_menu(request, db, usuario, exc.mensagem, codigo=404)
    contexto = {"r": resultado, "registrada": bool(registrada), **_contexto_usuario(usuario)}
    return templates.TemplateResponse(request, "tela3_resultados.html", contexto)


# --------------------------------------------------------------------------- #
# API REST
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["infra"])
def health() -> dict:
    return {"status": "ok", "versao": __version__}


@app.post("/api/auth/login", response_model=TokenOut, tags=["autenticação"])
def api_login(credenciais: LoginRequest, db: DbSession) -> TokenOut:
    usuario = AutenticacaoService(db).autenticar(credenciais.email, credenciais.senha)
    return TokenOut(access_token=criar_token_sessao(usuario.id))


@app.get("/api/programas", response_model=list[ProgramaPEIOut], tags=["PEI"])
def api_listar_programas(db: DbSession, _usuario: UsuarioAutenticado) -> list:
    return MonitoramentoService(db).listar_programas()


@app.post(
    "/api/sessoes",
    response_model=SessaoColetaOut,
    status_code=status.HTTP_201_CREATED,
    tags=["PEI"],
)
def api_registrar_sessao(
    dados: SessaoColetaCreate, db: DbSession, usuario: UsuarioAutenticado
) -> SessaoColetaOut:
    sessao = ColetaService(db).registrar_sessao(dados, usuario)
    return SessaoColetaOut.model_validate(sessao)


@app.get(
    "/api/programas/{programa_id}/resultados",
    response_model=ResultadoPrograma,
    tags=["PEI"],
)
def api_resultados(
    programa_id: int, db: DbSession, _usuario: UsuarioAutenticado
) -> ResultadoPrograma:
    return MonitoramentoService(db).resultado(programa_id)
