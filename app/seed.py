"""Carga inicial de dados de demonstração (idempotente).

Cria a supervisora Evelin Lubas, um aplicador, o paciente anonimizado
``PAC-2026-04`` (LGPD) e os programas PEI 01 e PEI 02. O PEI 01 recebe um
pequeno histórico de sessões para que a Tela 3 já exiba a evolução.

Execução manual: ``python -m app.seed``
"""

import os
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import (
    PERFIL_APLICADOR,
    PERFIL_SUPERVISOR,
    ProgramaPEI,
    SessaoColeta,
    Usuario,
    agora_utc,
)
from app.repositories import ProgramaPEIRepository, SessaoColetaRepository, UsuarioRepository
from app.security import hash_senha
from app.services import calcular_percentual_independencia, determinar_status

SENHA_PADRAO_DEMO = "Abrace@2026"
EMAIL_SUPERVISORA = "evelin.lubas@abrace.local"
EMAIL_APLICADOR = "aplicador@abrace.local"
PACIENTE_DEMO = "PAC-2026-04"

HISTORICO_PEI_01 = [
    ["I", "AP", "AT", "E", "AP", "I", "E", "AP", "I", "AT"],  # 30%
    ["I", "I", "AP", "I", "E", "AP", "I", "I", "AP", "I"],  # 60%
    ["I", "I", "I", "AP", "I", "I", "I", "E", "I", "I"],  # 80%
]


def popular_banco(db: Session, senha: str | None = None, com_historico: bool = True) -> bool:
    """Insere os dados de demonstração. Retorna ``False`` se já existiam."""
    usuarios = UsuarioRepository(db)
    if usuarios.obter_por_email(EMAIL_SUPERVISORA) is not None:
        return False

    senha = senha or os.getenv("SEED_SENHA") or SENHA_PADRAO_DEMO
    supervisora = usuarios.adicionar(
        Usuario(
            nome="Evelin Lubas",
            email=EMAIL_SUPERVISORA,
            senha_hash=hash_senha(senha),
            perfil=PERFIL_SUPERVISOR,
        )
    )
    aplicador = usuarios.adicionar(
        Usuario(
            nome="Aplicador ABA",
            email=EMAIL_APLICADOR,
            senha_hash=hash_senha(senha),
            perfil=PERFIL_APLICADOR,
        )
    )

    programas = ProgramaPEIRepository(db)
    pei_01 = programas.adicionar(
        ProgramaPEI(
            codigo="PEI 01",
            paciente_codigo=PACIENTE_DEMO,
            habilidade="Manter contato visual por 3 segundos ao ser chamado pelo nome",
            dominio="Comunicação Social",
            supervisor_id=supervisora.id,
        )
    )
    programas.adicionar(
        ProgramaPEI(
            codigo="PEI 02",
            paciente_codigo=PACIENTE_DEMO,
            habilidade="Imitar ações motoras grossas a partir de modelo",
            dominio="Imitação",
            supervisor_id=supervisora.id,
        )
    )

    if com_historico:
        sessoes = SessaoColetaRepository(db)
        inicio = agora_utc() - timedelta(days=len(HISTORICO_PEI_01))
        percentuais = []
        for dia, tentativas in enumerate(HISTORICO_PEI_01):
            percentual = calcular_percentual_independencia(tentativas)
            percentuais.append(percentual)
            sessoes.adicionar(
                SessaoColeta(
                    programa_id=pei_01.id,
                    aplicador_id=aplicador.id,
                    data_hora=inicio + timedelta(days=dia),
                    tentativas=",".join(tentativas),
                    total_tentativas=len(tentativas),
                    total_independentes=tentativas.count("I"),
                    percentual_independencia=percentual,
                )
            )
        pei_01.status = determinar_status(percentuais, pei_01.criterio_dominio)

    db.commit()
    return True


if __name__ == "__main__":  # pragma: no cover
    from app.database import SessionLocal, init_db

    init_db()
    with SessionLocal() as sessao:
        criado = popular_banco(sessao)
    print("Carga inicial aplicada." if criado else "Carga inicial já existente.")
