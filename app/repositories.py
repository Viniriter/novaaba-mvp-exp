"""Camada de Persistência: Padrão Repository sobre o SQLAlchemy 2.0.

Os repositórios encapsulam todo o acesso a dados e isolam a camada de serviço
dos detalhes do ORM e do banco. Eles apenas adicionam/consultam objetos; o
controle transacional (``commit``/``rollback``) fica com a camada de serviço,
que coordena operações envolvendo mais de um repositório.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ProgramaPEI, SessaoColeta, Usuario


class UsuarioRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def obter(self, usuario_id: int) -> Usuario | None:
        return self.db.get(Usuario, usuario_id)

    def obter_por_email(self, email: str) -> Usuario | None:
        consulta = select(Usuario).where(Usuario.email == email.strip().lower())
        return self.db.scalar(consulta)

    def adicionar(self, usuario: Usuario) -> Usuario:
        self.db.add(usuario)
        self.db.flush()
        return usuario


class ProgramaPEIRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def obter(self, programa_id: int) -> ProgramaPEI | None:
        return self.db.get(ProgramaPEI, programa_id)

    def listar(self) -> list[ProgramaPEI]:
        consulta = select(ProgramaPEI).order_by(ProgramaPEI.paciente_codigo, ProgramaPEI.codigo)
        return list(self.db.scalars(consulta))

    def adicionar(self, programa: ProgramaPEI) -> ProgramaPEI:
        self.db.add(programa)
        self.db.flush()
        return programa


class SessaoColetaRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def adicionar(self, sessao: SessaoColeta) -> SessaoColeta:
        self.db.add(sessao)
        self.db.flush()
        return sessao

    def listar_por_programa(self, programa_id: int) -> list[SessaoColeta]:
        consulta = (
            select(SessaoColeta)
            .where(SessaoColeta.programa_id == programa_id)
            .order_by(SessaoColeta.data_hora, SessaoColeta.id)
        )
        return list(self.db.scalars(consulta))
