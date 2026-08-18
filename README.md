# Sistema de Controle e Gestão de Absenteísmo

Aplicação web para registro de presença, monitoramento de absenteísmo e auditoria de apontamentos em linhas de produção. Desenvolvido em Flask com SQLAlchemy 2.0 e SQLite, oferece dashboards analíticos com agregações SQL puras e interface responsiva em Bootstrap 5.

## Funcionalidades

### 📊 Dashboard de Métricas
- **KPIs em tempo real**: Total de Funcionários, Taxa de Absenteísmo, Horas Perdidas, Registros no Período
- **Breakdowns agregados via SQL**: Absenteísmo por Linha, Projeto e Turno com `GROUP BY` e funções de agregação nativas (`COUNT`, `SUM`, `COALESCE`)
- **Tendência Diária**: Gráfico de ausências e minutos perdidos ao longo do período com uma única consulta `GROUP BY record_date`
- **Filtros dinâmicos cascatados**: Turno → Projeto → Linha com carregamento assíncrono via `/api/cascade-options`
- **Exportação Excel (.xlsx)**: Relatórios multi-aba (Indicadores, Por Linha, Registros Detalhados) via `pandas` + `openpyxl`
- **🏆 Top Colaboradores Ausentes**: Ranking com dias de ausência, ocorrências e Score Bradford (`/dashboard/api/top-absentees`)
- **Ausências por Dia da Semana**: Gráfico de barras com a distribuição semanal (`/dashboard/api/by-day-of-week`)

### 🧮 Fator Bradford (Window Functions SQL)
- Cálculo em **lote** de todos os funcionários ativos usando `LAG()` window function do SQLite
- Detecção de episódios (`spells`) consecutivos de ausência direto no banco — **zero processamento Python**
- Classificação de risco: Baixo (B < 50), Moderado (50–199), Alto (≥ 200)
- Tabela "Top Riscos" no dashboard com scores e contagem de episódios/dias

### 📝 Registro de Presença (Operação)
- Interface para Líderes com ações rápidas: Falta, Férias, Atraso, Saída Antecipada, Presente/Reset
- Time-lock de encerramento de turno (Líderes não podem alterar apontamentos 2h após fim do turno)
- Validação/Auditoria de Linha por dia
- Histórico rápido do funcionário com Bradford Factor em modal
- Busca dinâmica (Nome/ID) com debounce 300ms e submissão automática
- Paginação server-side com JOIN `Allocation + Employee` eliminando N+1 queries
- Seletor de registros por página (10, 25, 50, 100)

### 📋 Relatórios de Ausências
- Página dedicada `/reports/` com filtros: Período, Turno, Projeto, Linha, Tipo de Evento, Busca
- Tabela paginada com JOIN triplo `Attendance + Employee + Allocation`
- Exportação Excel (.xlsx) dos resultados filtrados
- Botão "Limpar Filtros" com recarga completa das opções do servidor

### 🔍 Logs de Auditoria Humanizados
- Ações traduzidas: `ATTENDANCE_CREATE` → "Apontamento Criado"
- Resumo em 1 linha na tabela principal (ex: "Falta Integral → Presente, 488 → 0 min")
- Modal Bootstrap com tabela **Campo | Antes | Depois**:
  - Campos internos ocultos (`allocation_id`, `registered_by_id`)
  - Datas formatadas `DD/MM/YYYY`, horários `HH:MM`
  - Status traduzidos (ex: `FULL_ABSENCE` → "Falta Integral")
  - Cores: vermelho no Antes, verde no Depois para alterações

### 🗓️ Calendário da Empresa (Feriados)
- Gestão de feriados nacionais, estaduais (SC) e municipais (Navegantes) em `/admin/calendar` (`models/calendar.py`)
- Pré-população automática via `holidays` + manual: **02/02** (Nossa Senhora dos Navegantes) e **26/08** (Emancipação de Navegantes)
- Dias marcados como `FERIADO`/`FOLGA_COMPENSADA` são **ignorados nas métricas de absenteísmo**
- Botão "🔄 Recarregar Feriados Nacionais/Locais" e idempotência garantida (`services/calendar_service.py`)

### 🔄 Transferências de Linha (Multi-Líder)
- Fluxo **PUSH** (enviar) e **PULL** (receber) com modal na tela de Operação
- Validação de escopo por `LeaderScope` (líderes só agem nas suas linhas); `ADMIN`/`SUPERVISOR` têm bypass e **auto-aprovação imediata**
- Fluxo de aprovação: líder dono da linha de origem (PULL) / destino (PUSH); requisições `PENDING` com aprovação/rejeição/cancelamento
- Badge de pendentes na navbar, log de movimentação (`EmployeeMovementLog`) e relatório exportável em `/reports/transfers`
- Ao aprovar, atualiza `Employee.line_id/shift_id` e a `Allocation` ativa (`routes/transfer.py`, `models/transfer.py`)

### 👥 Gestão de Funcionários (CRUD)
- CRUD completo em `/admin/employees` (criar, editar linha/turno/status, inativar via soft-delete preservando histórico)
- Papel **`SUPERVISOR`**: acesso administrativo (upload, linhas, turnos, calendário, relatórios) com card de Banco de Dados bloqueado (exclusivo `ADMIN`)

### ⚡ Otimização de Performance
- **Todas as agregações e filtros delegados ao SQLite** — zero loops Python para cálculo de métricas
- Índices compostos em `attendances` (`record_date + event_type`, `employee_id + record_date`) e `allocations` (`shift + line + end_date`)
- Subqueries com `LAG()` window function para Bradford Factor em lote
- `func.count().filter()` para agregações condicionais em consulta única
- Eliminação de N+1 queries via `JOIN` em todas as listagens

## Stack Técnica

| Camada | Tecnologia |
|---|---|
| **Backend** | Python 3.13, Flask, SQLAlchemy 2.0 (ORM + Core) |
| **Banco de Dados** | SQLite (com window functions `LAG`, `julianday`) |
| **Autenticação** | Flask-Login, Flask-Bcrypt (senhas hash) |
| **Frontend** | Bootstrap 5, Chart.js, Vanilla JavaScript ES6 |
| **Exportação** | pandas, openpyxl (.xlsx) |
| **Calendário/Feriados** | holidays (nacionais, SC e municipais) |
| **Servidor WSGI** | Waitress (produção) |

## Estrutura do Projeto

```
projeto_absenteismo/
├── app.py                   # Ponto de entrada, factory app, endpoint /api/cascade-options
├── config.py                # Configurações (SQLite URI, secret key, tolerâncias)
├── extensions.py            # db, login_manager, bcrypt
├── requirements.txt         # Dependências
├── seed.py                  # Criação de usuários iniciais (admin/lider/supervisor)
├── models/
│   ├── employee.py          # Modelo Employee (linha/turno/projeto/status)
│   ├── allocation.py        # Alocações (turno, projeto, linha) com índices compostos
│   ├── attendance.py        # Registros de presença com índices compostos
│   ├── user.py              # Usuários do sistema (LIDER/SUPERVISOR/ADMIN)
│   ├── shift.py             # Definições de turno
│   ├── line.py              # Linhas e projetos
│   ├── audit_log.py         # Logs de auditoria (JSON old/new)
│   ├── calendar.py          # Calendário da empresa (FERIADO/FOLGA_COMPENSADA/SABADO_LETIVO)
│   ├── transfer.py          # TransferRequests e EmployeeMovementLog
│   └── line_validation.py   # Validação de linha por dia
├── routes/
│   ├── auth.py              # Login/logout
│   ├── leader.py            # Tela de operação (registro de presença)
│   ├── dashboard.py         # APIs do dashboard (agregações SQL)
│   ├── admin.py             # Painel admin, upload, usuários, CRUD, calendário
│   ├── transfer.py          # Fluxo de transferências (PUSH/PULL, aprovação)
│   └── reports.py           # Relatórios de ausências + transferências + exportação
├── services/
│   ├── metrics_service.py   # Cálculo de minutos perdidos, Bradford Factor (SQL LAG)
│   ├── calendar_service.py  # Pré-população de feriados oficiais
│   └── excel_service.py     # Processamento de upload, exportação Excel
├── static/js/
│   ├── filters.js           # Utilitário compartilhado: cascade, clear, debounce
│   ├── dashboard.js         # Gráficos Chart.js + carregamento AJAX
│   ├── leader.js            # Auto-fill de horários nos modais
│   └── transfers.js         # Modal de transferência (PUSH/PULL dinâmico)
└── templates/
    ├── base.html            # Layout base com navbar + badge de pendências
    ├── login.html
    ├── employee_history.html
    ├── admin/               # Dashboard, upload, usuários, shifts, calendar, CRUD
    ├── leader/              # Registro de presença (index)
    ├── transfers/           # Revisão/aprovação de transferências
    └── reports/             # Consulta de ausências + histórico de transferências
```

## Instalação e Execução

### Pré-requisitos
- Python 3.11+
- pip

### Passos

```bash
# 1. Clone o repositório
git clone <url-do-repositorio>
cd projeto-absenteismo

# 2. Crie o ambiente virtual
python -m venv venv

# 3. Ative o ambiente virtual
# Windows:
venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 4. Instale as dependências
pip install -r requirements.txt

# 5. Execute a aplicação
python app.py
```

A aplicação estará disponível em `http://localhost:5000`.

### Credenciais Padrão

| Usuário | Senha | Papel |
|---|---|---|
| `admin` | `admin123` | ADMIN — acesso total |
| `supervisor` | `supervisor123` | SUPERVISOR — administrativo (sem gestão de banco) |
| `lider` | `lider123` | LIDER — registro de presença e dashboard |

> As senhas são criadas automaticamente na primeira execução via `seed.py`. Altere-as após o primeiro login.

### Ambiente de Produção

```bash
python run_production.py
```

Utiliza o servidor WSGI **Waitress** na porta 5000.

## Variáveis de Ambiente (.env)

| Variável | Descrição | Padrão |
|---|---|---|
| `SECRET_KEY` | Chave secreta Flask | Gerada automaticamente |
| `DATABASE_URL` | URL de conexão (SQLite) | `sqlite:///absenteeism.db` |

---

**Licença:** Uso interno.