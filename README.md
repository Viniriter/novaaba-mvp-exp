# NovaABA (Módulo de Coleta e Monitoramento de PEI)

Aplicação web responsiva (*Mobile-First*) desenvolvida como Produto Mínimo Viável (MVP) para digitalizar a coleta de tentativas do **Plano de Ensino Individualizado (PEI)** em sessões de terapia ABA para crianças com Transtorno do Espectro Autista (TEA) na **Clínica Abrace** (Campo Grande, MS).

## Objetivo e Alinhamento aos ODS (ONU)
* **Problema resolvido:** Eliminação do uso de fichas em prancheta de papel, das 3 horas semanais de retrabalho digitando dados em planilhas e da defasagem de 3 a 5 dias na atualização dos gráficos clínicos.
* **ODS 3 (Saúde e Bem-Estar):** Metas 3.4 e 3.8.
* **ODS 4 (Educação de Qualidade):** Meta 4.2 (desenvolvimento na primeira infância).

## Arquitetura e Tecnologias
O sistema adota **Arquitetura Monolítica Modular em Camadas** com **Padrão Repository**:
* **Linguagem e Runtime:** Python 3.12
* **Camada de Apresentação:** Jinja2 Templates + Tailwind CSS (WCAG 2.1 AA, alvos de toque >= 48px e tripla codificação visual)
* **Camada de Controle e Validação:** FastAPI + Pydantic 2.6+
* **Camada de Serviço:** Regras clínicas de cálculo automático de independência e verificação do critério de domínio (80%)
* **Camada de Persistência:** SQLAlchemy 2.0+ (PostgreSQL em produção / SQLite em memória para bateria de testes automatizados)
* **Segurança:** Autenticação com hash robusto Bcrypt e anonimização de pacientes em conformidade com a LGPD (`PAC-2026-04`).

## Estrutura do Projeto
```text
novaaba-mvp/
├── app/
│   ├── main.py            # Ponto de entrada FastAPI e rotas da aplicação
│   ├── models.py          # Entidades SQLAlchemy (usuario, programa_pei, sessao_coleta)
│   ├── schemas.py         # Validação de entrada Pydantic (I, AP, AT, E)
│   ├── services.py        # Cálculo percentual de independência e status do PEI
│   ├── repositories.py    # Abstração de acesso a dados (Padrão Repository)
│   └── templates/         # Interfaces Mobile-First (Telas 1, 2 e 3)
├── tests/
│   ├── test_unit.py       # Testes unitários da camada de serviço clínico
│   └── test_integration.py# Testes de integração das rotas da API REST
├── .github/workflows/
│   └── ci.yml             # Pipeline de Integração Contínua (GitHub Actions)
└── requirements.txt       # Dependências do projeto
