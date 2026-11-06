"""Configuração da camada de persistência (SQLAlchemy 2.0).

A URL do banco é lida da variável de ambiente ``DATABASE_URL``. Em produção
aponta para PostgreSQL; localmente e no CI usa SQLite como fallback, para que
testes e demonstração rodem sem depender de um banco externo ativo.
"""

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

SQLITE_FALLBACK_URL = "sqlite:///./novaaba.db"


def resolver_database_url() -> str:
    """Retorna a URL do banco, normalizando o prefixo legado ``postgres://``."""
    url = os.getenv("DATABASE_URL", "").strip() or SQLITE_FALLBACK_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def criar_engine(url: str) -> Engine:
    """Cria o engine aplicando opções específicas do SQLite quando necessário."""
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


DATABASE_URL = resolver_database_url()
engine = criar_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Classe base declarativa de todos os modelos ORM."""


def init_db(bind: Engine | None = None) -> None:
    """Cria as tabelas caso ainda não existam."""
    from app import models  # noqa: F401  (registra os modelos no metadata)

    Base.metadata.create_all(bind=bind or engine)


def get_db() -> Generator[Session, None, None]:
    """Dependência FastAPI que fornece uma sessão de banco por requisição."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
