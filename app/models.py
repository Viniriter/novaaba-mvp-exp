"""Entidades ORM: Usuario, ProgramaPEI e SessaoColeta.

LGPD: o paciente NÃO possui tabela própria nem dados pessoais armazenados.
Ele é referenciado apenas por um código pseudonimizado (ex.: ``PAC-2026-04``),
validado no momento da atribuição.
"""

import re
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base

PADRAO_CODIGO_PACIENTE = re.compile(r"^PAC-\d{4}-\d{2,4}$")

PERFIL_SUPERVISOR = "supervisor"
PERFIL_APLICADOR = "aplicador"
PERFIS_VALIDOS = frozenset({PERFIL_SUPERVISOR, PERFIL_APLICADOR})

STATUS_NAO_INICIADO = "NAO_INICIADO"
STATUS_EM_AQUISICAO = "EM_AQUISICAO"
STATUS_DOMINADO = "DOMINADO"
STATUS_VALIDOS = frozenset({STATUS_NAO_INICIADO, STATUS_EM_AQUISICAO, STATUS_DOMINADO})


def agora_utc() -> datetime:
    """Data/hora atual em UTC (sem tzinfo, para portabilidade SQLite/PostgreSQL)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Usuario(Base):
    """Profissional da clínica (supervisor(a) ou aplicador(a))."""

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(60))
    perfil: Mapped[str] = mapped_column(String(20), default=PERFIL_APLICADOR)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)

    programas_supervisionados: Mapped[list["ProgramaPEI"]] = relationship(
        back_populates="supervisor"
    )
    sessoes_aplicadas: Mapped[list["SessaoColeta"]] = relationship(
        back_populates="aplicador"
    )

    @validates("perfil")
    def _validar_perfil(self, _chave: str, perfil: str) -> str:
        if perfil not in PERFIS_VALIDOS:
            raise ValueError(f"Perfil inválido: {perfil!r}")
        return perfil

    @validates("email")
    def _normalizar_email(self, _chave: str, email: str) -> str:
        return email.strip().lower()


class ProgramaPEI(Base):
    """Programa (meta) do Plano de Ensino Individualizado de um paciente."""

    __tablename__ = "programas_pei"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20))
    paciente_codigo: Mapped[str] = mapped_column(String(20), index=True)
    habilidade: Mapped[str] = mapped_column(String(160))
    dominio: Mapped[str] = mapped_column(String(60))
    criterio_dominio: Mapped[float] = mapped_column(Float, default=80.0)
    status: Mapped[str] = mapped_column(String(20), default=STATUS_NAO_INICIADO)
    supervisor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)

    supervisor: Mapped[Usuario] = relationship(back_populates="programas_supervisionados")
    sessoes: Mapped[list["SessaoColeta"]] = relationship(
        back_populates="programa",
        order_by="(SessaoColeta.data_hora, SessaoColeta.id)",
        cascade="all, delete-orphan",
    )

    @validates("paciente_codigo")
    def _validar_codigo_anonimizado(self, _chave: str, codigo: str) -> str:
        codigo = codigo.strip().upper()
        if not PADRAO_CODIGO_PACIENTE.fullmatch(codigo):
            raise ValueError(
                "O paciente deve ser identificado apenas por código anonimizado "
                "no formato PAC-AAAA-NN (LGPD)."
            )
        return codigo

    @validates("status")
    def _validar_status(self, _chave: str, status: str) -> str:
        if status not in STATUS_VALIDOS:
            raise ValueError(f"Status inválido: {status!r}")
        return status


class SessaoColeta(Base):
    """Sessão de coleta por tentativas discretas de um programa PEI.

    ``tentativas`` guarda a sequência registrada (ex.: ``"I,AP,E,I"``); os
    totais e o percentual são persistidos para consultas e relatórios.
    """

    __tablename__ = "sessoes_coleta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    programa_id: Mapped[int] = mapped_column(ForeignKey("programas_pei.id"), index=True)
    aplicador_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"), index=True)
    data_hora: Mapped[datetime] = mapped_column(DateTime, default=agora_utc)
    tentativas: Mapped[str] = mapped_column(String(255))
    total_tentativas: Mapped[int] = mapped_column(Integer)
    total_independentes: Mapped[int] = mapped_column(Integer)
    percentual_independencia: Mapped[float] = mapped_column(Float)
    observacoes: Mapped[str | None] = mapped_column(Text, nullable=True)

    programa: Mapped[ProgramaPEI] = relationship(back_populates="sessoes")
    aplicador: Mapped[Usuario] = relationship(back_populates="sessoes_aplicadas")

    @property
    def lista_tentativas(self) -> list[str]:
        return [t for t in self.tentativas.split(",") if t]
