"""Camada de Serviço: regras clínicas ABA e casos de uso do PEI.

As regras de cálculo são funções puras (sem banco), o que permite testá-las de
forma unitária e isolada. As classes de serviço orquestram os repositórios e
controlam as transações.
"""

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.models import (
    STATUS_DOMINADO,
    STATUS_EM_AQUISICAO,
    STATUS_NAO_INICIADO,
    ProgramaPEI,
    SessaoColeta,
    Usuario,
)
from app.repositories import ProgramaPEIRepository, SessaoColetaRepository, UsuarioRepository
from app.schemas import (
    TENTATIVAS_VALIDAS,
    ProgramaPEIOut,
    ResultadoPrograma,
    SessaoColetaCreate,
    SessaoColetaOut,
)
from app.security import hash_senha, verificar_senha

CRITERIO_DOMINIO_PADRAO = 80.0
SESSOES_CONSECUTIVAS_DOMINIO = 3


# --------------------------------------------------------------------------- #
# Exceções de negócio
# --------------------------------------------------------------------------- #
class ServiceError(Exception):
    """Erro de negócio com o status HTTP correspondente."""

    status_code = 400

    def __init__(self, mensagem: str) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem


class CredenciaisInvalidas(ServiceError):
    status_code = 401


class RecursoNaoEncontrado(ServiceError):
    status_code = 404


# --------------------------------------------------------------------------- #
# Funções puras de cálculo ABA
# --------------------------------------------------------------------------- #
def calcular_percentual_independencia(tentativas: Sequence[str]) -> float:
    """Percentual de tentativas independentes (``I``) sobre o total, 1 casa decimal."""
    invalidas = [t for t in tentativas if t not in TENTATIVAS_VALIDAS]
    if invalidas:
        raise ValueError(f"Tentativas inválidas: {invalidas}")
    if not tentativas:
        return 0.0
    independentes = sum(1 for t in tentativas if t == "I")
    return round(independentes / len(tentativas) * 100, 1)


def contar_sessoes_consecutivas_no_criterio(
    percentuais: Sequence[float], criterio: float = CRITERIO_DOMINIO_PADRAO
) -> int:
    """Quantas sessões, a partir da mais recente, estão no critério ou acima dele."""
    contador = 0
    for percentual in reversed(percentuais):
        if percentual < criterio:
            break
        contador += 1
    return contador


def verificar_criterio_dominio(
    percentuais: Sequence[float],
    criterio: float = CRITERIO_DOMINIO_PADRAO,
    sessoes_consecutivas: int = SESSOES_CONSECUTIVAS_DOMINIO,
) -> bool:
    """Domínio atingido quando as últimas N sessões consecutivas estão ≥ critério."""
    return contar_sessoes_consecutivas_no_criterio(percentuais, criterio) >= sessoes_consecutivas


def determinar_status(
    percentuais: Sequence[float],
    criterio: float = CRITERIO_DOMINIO_PADRAO,
    sessoes_consecutivas: int = SESSOES_CONSECUTIVAS_DOMINIO,
) -> str:
    """Status do programa PEI a partir do histórico de percentuais."""
    if not percentuais:
        return STATUS_NAO_INICIADO
    if verificar_criterio_dominio(percentuais, criterio, sessoes_consecutivas):
        return STATUS_DOMINADO
    return STATUS_EM_AQUISICAO


def calcular_media(percentuais: Sequence[float]) -> float:
    return round(sum(percentuais) / len(percentuais), 1) if percentuais else 0.0


# --------------------------------------------------------------------------- #
# Casos de uso
# --------------------------------------------------------------------------- #
class AutenticacaoService:
    _hash_fantasma: str | None = None

    def __init__(self, db: Session) -> None:
        self.usuarios = UsuarioRepository(db)

    @classmethod
    def _hash_para_tempo_constante(cls) -> str:
        if cls._hash_fantasma is None:
            cls._hash_fantasma = hash_senha("usuario-inexistente")
        return cls._hash_fantasma

    def autenticar(self, email: str, senha: str) -> Usuario:
        usuario = self.usuarios.obter_por_email(email)
        if usuario is None:
            # Executa um bcrypt mesmo assim para não revelar, pelo tempo de
            # resposta, se o e-mail existe (enumeração de usuários).
            verificar_senha(senha, self._hash_para_tempo_constante())
            raise CredenciaisInvalidas("E-mail ou senha inválidos.")
        if not verificar_senha(senha, usuario.senha_hash) or not usuario.ativo:
            raise CredenciaisInvalidas("E-mail ou senha inválidos.")
        return usuario

    def obter_usuario_ativo(self, usuario_id: int | None) -> Usuario | None:
        if usuario_id is None:
            return None
        usuario = self.usuarios.obter(usuario_id)
        return usuario if usuario is not None and usuario.ativo else None


class ColetaService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.programas = ProgramaPEIRepository(db)
        self.sessoes = SessaoColetaRepository(db)

    def registrar_sessao(self, dados: SessaoColetaCreate, aplicador: Usuario) -> SessaoColeta:
        """Persiste a sessão e atualiza automaticamente o status do programa PEI."""
        programa = self.programas.obter(dados.programa_id)
        if programa is None:
            raise RecursoNaoEncontrado("Programa PEI não encontrado.")

        tentativas = list(dados.tentativas)
        try:
            sessao = self.sessoes.adicionar(
                SessaoColeta(
                    programa_id=programa.id,
                    aplicador_id=aplicador.id,
                    tentativas=",".join(tentativas),
                    total_tentativas=len(tentativas),
                    total_independentes=tentativas.count("I"),
                    percentual_independencia=calcular_percentual_independencia(tentativas),
                    observacoes=dados.observacoes,
                )
            )
            self._atualizar_status(programa)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        self.db.refresh(sessao)
        return sessao

    def _atualizar_status(self, programa: ProgramaPEI) -> None:
        percentuais = [
            s.percentual_independencia for s in self.sessoes.listar_por_programa(programa.id)
        ]
        programa.status = determinar_status(percentuais, programa.criterio_dominio)


class MonitoramentoService:
    def __init__(self, db: Session) -> None:
        self.programas = ProgramaPEIRepository(db)
        self.sessoes = SessaoColetaRepository(db)

    def listar_programas(self) -> list[ProgramaPEI]:
        return self.programas.listar()

    def obter_programa(self, programa_id: int) -> ProgramaPEI:
        programa = self.programas.obter(programa_id)
        if programa is None:
            raise RecursoNaoEncontrado("Programa PEI não encontrado.")
        return programa

    def resultado(self, programa_id: int) -> ResultadoPrograma:
        programa = self.obter_programa(programa_id)
        sessoes = self.sessoes.listar_por_programa(programa.id)
        percentuais = [s.percentual_independencia for s in sessoes]
        return ResultadoPrograma(
            programa=ProgramaPEIOut.model_validate(programa),
            total_sessoes=len(sessoes),
            media_independencia=calcular_media(percentuais),
            ultimo_percentual=percentuais[-1] if percentuais else None,
            sessoes_consecutivas_exigidas=SESSOES_CONSECUTIVAS_DOMINIO,
            sessoes_consecutivas_no_criterio=contar_sessoes_consecutivas_no_criterio(
                percentuais, programa.criterio_dominio
            ),
            sessoes=[SessaoColetaOut.model_validate(s) for s in sessoes],
        )
