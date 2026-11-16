# NovaABA — Módulo de Coleta e Monitoramento de PEI

[![CI](https://github.com/Viniriter/NovaABA-mvp-exp/actions/workflows/ci.yml/badge.svg)](https://github.com/Viniriter/NovaABA-mvp-exp/actions/workflows/ci.yml)

Aplicação web responsiva (*Mobile-First*) desenvolvida como Produto Mínimo Viável (MVP) para digitalizar a coleta de tentativas do **Plano de Ensino Individualizado (PEI)** em sessões de terapia ABA (*Applied Behavior Analysis*) para crianças com Transtorno do Espectro Autista (TEA) na **Clínica Abrace** (Campo Grande, MS).

> Projeto acadêmico — **Experiência Prática III** da disciplina de **Projeto de Software**.

---

## 1. Problema e objetivo

| Situação atual (papel) | Com o NovaABA |
| --- | --- |
| Fichas em prancheta de papel durante a sessão | Registro tentativa a tentativa em botões grandes no celular/tablet |
| ~3 horas semanais de retrabalho digitando dados em planilhas | Dado persistido no momento da coleta, sem redigitação |
| Gráficos clínicos atualizados com 3 a 5 dias de atraso | Percentual de independência e status do PEI calculados na hora |

### Alinhamento aos Objetivos de Desenvolvimento Sustentável (ONU)

* **ODS 3 — Saúde e Bem-Estar:** metas **3.4** (promoção da saúde mental e do bem-estar) e **3.8** (acesso a serviços de saúde de qualidade).
* **ODS 4 — Educação de Qualidade:** meta **4.2** (desenvolvimento na primeira infância).

## 2. Funcionalidades do MVP

* **Tela 1 — Menu (`GET /`):** login do profissional e lista dos programas PEI do paciente, com o status clínico de cada um.
* **Tela 2 — Coleta (`GET /coleta/{id}`):** registro das tentativas da sessão com quatro botões de resposta:

  | Código | Significado | Codificação visual (cor + símbolo + texto) |
  | :---: | --- | --- |
  | `I`  | Independente | verde · ✔ · "Independente" |
  | `AP` | Ajuda Parcial | âmbar · ◐ · "Ajuda Parcial" |
  | `AT` | Ajuda Total | laranja · ● · "Ajuda Total" |
  | `E`  | Erro | vermelho · ✖ · "Erro" |

* **Tela 3 — Resultados (`GET /resultados/{id}`):** histórico das sessões, gráfico de barras do percentual de independência com a linha do critério de 80% e o status atualizado do PEI.

### Regras clínicas (camada de serviço)

* **Percentual de independência** = `tentativas "I" ÷ total de tentativas × 100` (uma casa decimal).
* **Critério de domínio:** o programa passa automaticamente para **Dominado** quando as **3 últimas sessões consecutivas** atingem **≥ 80%** de independência; caso contrário, permanece **Em aquisição** (ou **Não iniciado**, sem sessões).

## 3. Arquitetura

**Monolito Modular em Camadas** com **Padrão Repository**:

```text
┌──────────────────────────────────────────────────────────────┐
│ Apresentação        Jinja2 + Tailwind CSS (templates/)       │
├──────────────────────────────────────────────────────────────┤
│ Controle/Validação  FastAPI (main.py) + Pydantic (schemas.py)│
│                     + autenticação/sessão (security.py)      │
├──────────────────────────────────────────────────────────────┤
│ Serviço             Regras clínicas ABA (services.py)        │
├──────────────────────────────────────────────────────────────┤
│ Persistência        Repositories (repositories.py)           │
│                     SQLAlchemy 2.0 (models.py, database.py)  │
└──────────────────────────────────────────────────────────────┘
          PostgreSQL (produção)  |  SQLite (local / CI)
```

Cada camada depende apenas da camada imediatamente inferior: as rotas não acessam o ORM diretamente, e as regras de cálculo são **funções puras**, testáveis sem banco de dados.

### Modelo de dados

| Entidade | Tabela | Descrição |
| --- | --- | --- |
| `Usuario` | `usuarios` | Profissionais (perfil `supervisor` ou `aplicador`), senha armazenada somente como hash Bcrypt |
| `ProgramaPEI` | `programas_pei` | Programa/meta do PEI, vinculado ao paciente **apenas pelo código anonimizado** (ex.: `PAC-2026-04`) e ao supervisor responsável (FK `usuarios.id`) |
| `SessaoColeta` | `sessoes_coleta` | Sessão de coleta com a sequência de tentativas, totais e percentual calculados (FKs `programas_pei.id` e `usuarios.id`) |

## 4. Stack tecnológica

| Camada | Tecnologia |
| --- | --- |
| Linguagem / runtime | Python 3.12+ |
| Web / API | FastAPI ≥ 0.110, Uvicorn ≥ 0.29 |
| Validação | Pydantic ≥ 2.6 |
| Persistência | SQLAlchemy ≥ 2.0 (PostgreSQL em produção, SQLite como fallback) |
| Interface | Jinja2 ≥ 3.1 + Tailwind CSS (CDN) |
| Segurança | Bcrypt ≥ 4.1 (fator de custo 12), cookies `HttpOnly` assinados com HMAC-SHA256 |
| Testes | Pytest ≥ 8.1, pytest-cov ≥ 5.0, HTTPX ≥ 0.27 |
| CI | GitHub Actions |

## 5. Segurança e conformidade

* **LGPD — anonimização:** nenhum dado pessoal do paciente é armazenado. O paciente é identificado somente pelo código pseudonimizado no formato `PAC-AAAA-NN` (ex.: `PAC-2026-04`), validado no modelo.
* **Senhas (OWASP A02/A07):** hash **Bcrypt com fator de custo 12** e salt aleatório; texto limpo, MD5 ou SHA-1 nunca são usados.
* **Injeção / XSS (OWASP A03):** tentativas restritas ao domínio fechado `{"I", "AP", "AT", "E"}`; observações têm tags HTML e caracteres de controle removidos antes de persistir, e o Jinja2 aplica *autoescape* na saída. O ORM usa consultas parametrizadas.
* **Controle de acesso (OWASP A01):** telas e API exigem sessão autenticada (cookie `HttpOnly`, `SameSite=Lax`, assinado e com expiração ou cabeçalho `Authorization: Bearer`).

## 6. Acessibilidade (RNF04)

* Alvos de toque com **altura mínima de 52px**.
* **Foco visível** (`focus-visible:ring`) em todos os elementos interativos.
* Paleta com **contraste WCAG 2.1 AA** (≥ 4,5:1 para texto).
* **Tripla codificação** (cor + símbolo + texto) nos botões de tentativa e nos status do PEI, sem depender apenas da cor.
* HTML semântico, `lang="pt-BR"`, rótulos associados aos campos e região `aria-live` na coleta.

## 7. Estrutura do projeto

```text
NovaABA-mvp-exp/
├── .github/workflows/ci.yml   # Pipeline de Integração Contínua (GitHub Actions)
├── app/
│   ├── __init__.py
│   ├── database.py            # Engine/Session SQLAlchemy via DATABASE_URL
│   ├── models.py              # Entidades Usuario, ProgramaPEI e SessaoColeta
│   ├── schemas.py             # Validação Pydantic (I, AP, AT, E) e sanitização XSS
│   ├── security.py            # Hash Bcrypt e tokens de sessão assinados
│   ├── repositories.py        # Padrão Repository (acesso a dados)
│   ├── services.py            # Cálculo de independência e status do PEI
│   ├── seed.py                # Carga inicial de dados de demonstração
│   ├── main.py                # Aplicação FastAPI: rotas HTML e API REST
│   └── templates/
│       ├── tela1_menu.html
│       ├── tela2_coleta.html
│       └── tela3_resultados.html
├── tests/
│   ├── __init__.py
│   ├── test_unit.py           # Funções puras ABA, Pydantic e Bcrypt
│   └── test_integration.py    # Rotas FastAPI, persistência e controle de acesso
├── .gitignore
├── requirements.txt
└── README.md
```

## 8. Como executar

### Pré-requisitos

* Python **3.12** ou superior
* Git

### Passo a passo

```bash
git clone https://github.com/Viniriter/NovaABA-mvp-exp.git
cd NovaABA-mvp-exp

python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows (PowerShell)
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
uvicorn app.main:app --reload
```

Acesse **http://127.0.0.1:8000**. Na primeira execução as tabelas são criadas e a carga inicial (`app/seed.py`) é aplicada automaticamente. A documentação interativa da API fica em **http://127.0.0.1:8000/docs**.

Para recriar os dados manualmente: `python -m app.seed`.

### Usuários de demonstração

| Perfil | Nome | E-mail | Senha |
| --- | --- | --- | --- |
| Supervisora | Evelin Lubas | `evelin.lubas@abrace.local` | `Abrace@2026` |
| Aplicador | Aplicador ABA | `aplicador@abrace.local` | `Abrace@2026` |

> As credenciais acima existem apenas para demonstração local. Em produção, defina `SEED_SENHA` e `SECRET_KEY`.

### Variáveis de ambiente

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./novaaba.db` | URL do banco. Ex. PostgreSQL: `postgresql+psycopg://usuario:senha@host:5432/novaaba` (requer `pip install "psycopg[binary]"`) |
| `SECRET_KEY` | chave de desenvolvimento | Chave HMAC usada para assinar os tokens de sessão |
| `SEED_SENHA` | `Abrace@2026` | Senha atribuída aos usuários da carga inicial |
| `SEED_ON_STARTUP` | `1` | Use `0` para não aplicar a carga inicial ao iniciar |

## 9. Testes

```bash
pytest --cov=app --cov-report=term-missing
```

* `tests/test_unit.py`: funções puras de cálculo ABA, validações Pydantic (domínio de tentativas e sanitização XSS) e hash Bcrypt.
* `tests/test_integration.py`: rotas HTML e API do FastAPI, persistência em banco SQLite em memória e controle de acesso (OWASP).

## 10. Integração contínua

O workflow [`.github/workflows/ci.yml`](.github/workflows/ci.yml) roda a cada *push* e *pull request*: instala as dependências em Python 3.12, compila os módulos e executa a suíte de testes com relatório de cobertura.

## 11. API REST (resumo)

| Método | Rota | Autenticação | Descrição |
| --- | --- | :---: | --- |
| `GET` | `/health` | — | Verificação de saúde |
| `POST` | `/api/auth/login` | — | Retorna o token de acesso |
| `GET` | `/api/programas` | ✔ | Lista os programas PEI e seus status |
| `POST` | `/api/sessoes` | ✔ | Registra uma sessão de coleta |
| `GET` | `/api/programas/{id}/resultados` | ✔ | Indicadores e histórico do programa |

## Licença

Projeto acadêmico de uso educacional.
