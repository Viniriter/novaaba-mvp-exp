"""Camada de Controle/Validação: contratos de entrada e saída (Pydantic v2).

* As tentativas aceitam estritamente o domínio fechado ``{"I", "AP", "AT", "E"}``.
* As observações são sanitizadas contra XSS (OWASP A03): blocos de script/estilo
  e tags HTML são removidos, assim como caracteres de controle, antes de chegar
  à camada de serviço. Na saída, o Jinja2 ainda aplica *autoescape*.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Tentativa = Literal["I", "AP", "AT", "E"]
TENTATIVAS_VALIDAS: frozenset[str] = frozenset({"I", "AP", "AT", "E"})
ROTULOS_TENTATIVA = {
    "I": "Independente",
    "AP": "Ajuda Parcial",
    "AT": "Ajuda Total",
    "E": "Erro",
}
MAX_TENTATIVAS_POR_SESSAO = 50
MAX_OBSERVACOES = 500

_RE_BLOCO_PERIGOSO = re.compile(
    r"<\s*(script|style|iframe|object|embed)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
# Tag HTML: "<" seguido de letra, "/" ou "!" (não remove textos como "< 3 >").
_RE_TAG = re.compile(r"<\s*[/!]?\s*[a-zA-Z][^>]*>|<!--.*?-->", re.DOTALL)
_RE_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_RE_ESQUEMA_PERIGOSO = re.compile(r"(javascript|vbscript|data)\s*:", re.IGNORECASE)


def sanitizar_texto(valor: str | None) -> str | None:
    """Remove conteúdo ativo/HTML de um texto livre; retorna ``None`` se ficar vazio."""
    if valor is None:
        return None
    texto = _RE_BLOCO_PERIGOSO.sub("", valor)
    texto = _RE_TAG.sub("", texto)
    texto = _RE_ESQUEMA_PERIGOSO.sub("", texto)
    texto = _RE_CONTROLE.sub("", texto)
    texto = texto.replace("<", "").replace(">", "").strip()
    return texto or None


# --------------------------------------------------------------------------- #
# Entrada
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=160, pattern=r"^[^@\s]+@[^@\s]+$")
    senha: str = Field(min_length=1, max_length=128)

    @field_validator("email", mode="before")
    @classmethod
    def normalizar_email(cls, valor: object) -> object:
        return valor.strip().lower() if isinstance(valor, str) else valor


class SessaoColetaCreate(BaseModel):
    """Sessão de coleta enviada pelo aplicador."""

    programa_id: int = Field(gt=0)
    tentativas: list[Tentativa] = Field(min_length=1, max_length=MAX_TENTATIVAS_POR_SESSAO)
    observacoes: str | None = Field(default=None, max_length=MAX_OBSERVACOES)

    @field_validator("tentativas", mode="before")
    @classmethod
    def normalizar_tentativas(cls, valor: object) -> object:
        """Aceita lista ou texto separado por vírgulas (formulário HTML)."""
        if isinstance(valor, str):
            valor = [parte for parte in valor.split(",") if parte.strip()]
        if isinstance(valor, list):
            return [v.strip().upper() if isinstance(v, str) else v for v in valor]
        return valor

    @field_validator("observacoes")
    @classmethod
    def sanitizar_observacoes(cls, valor: str | None) -> str | None:
        return sanitizar_texto(valor)


# --------------------------------------------------------------------------- #
# Saída
# --------------------------------------------------------------------------- #
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    perfil: str


class ProgramaPEIOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    paciente_codigo: str
    habilidade: str
    dominio: str
    criterio_dominio: float
    status: str


class SessaoColetaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    programa_id: int
    aplicador_id: int
    data_hora: datetime
    tentativas: list[str]
    total_tentativas: int
    total_independentes: int
    percentual_independencia: float
    observacoes: str | None

    @field_validator("tentativas", mode="before")
    @classmethod
    def separar_tentativas(cls, valor: object) -> object:
        if isinstance(valor, str):
            return [t for t in valor.split(",") if t]
        return valor


class ResultadoPrograma(BaseModel):
    programa: ProgramaPEIOut
    total_sessoes: int
    media_independencia: float
    ultimo_percentual: float | None
    sessoes_consecutivas_exigidas: int
    sessoes_consecutivas_no_criterio: int
    sessoes: list[SessaoColetaOut]
