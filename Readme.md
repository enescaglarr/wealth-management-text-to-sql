# Wealth Management Text-to-SQL

Wealth Management Text-to-SQL is a data warehouse — advisors, clients, accounts, assets, transactions,
portfolios, projections — that you can query in plain English. It pairs a SQL Server schema (views and
stored procedures included, seeded with Faker-generated synthetic data) with two natural-language-to-SQL
approaches: a **Vanna AI** model grounded in the warehouse's own schema *and* hand-written documentation
(RAG over ChromaDB), and a lighter **LangChain SQL chain** that sees only the live schema. A 30-question
evaluation measures them against each other — **100% vs 83% execution accuracy** — and every generated
query passes a parser-level read-only guard before it reaches the database. Everything runs on **Groq**
and a local SQL Server container, so the project costs nothing to run.

## Quick Start

```bash
git clone https://github.com/enescaglarr/WealthManagement-TextToSQL.git
cd WealthManagement-TextToSQL
cp .env.example .env      # fill in GROQ_API_KEY and pick a DB_PASSWORD
./run.sh
```

That is the whole setup. `run.sh` creates the virtualenv, starts a SQL Server container, creates and seeds
the warehouse, trains the retrieval index and opens the web app at http://localhost:8085 — each step is
skipped if it has already been done.

| Flag | What it does |
|---|---|
| `./run.sh` | start everything and open the web app |
| `./run.sh --ask "question"` | answer one question through the LangChain path instead |
| `./run.sh --eval` | re-run the 30-question evaluation → [eval/results.md](eval/results.md) |
| `./run.sh --ablation` | run the training-data ablation → `eval/ablation.md` |
| `./run.sh --train` | retrain the retrieval index (after editing the schema or documentation) |
| `./run.sh --reset` | delete the container and the index and rebuild from scratch |
| `./run.sh --vanna-ui` | Vanna's bundled UI on :8084, for comparison |

## What it costs to run

| Component | Cost | Notes |
|---|---|---|
| Groq API | Free | The free tier is enough; one question is one request |
| ChromaDB | Free | A local file (`RAGToSQL/chroma.sqlite3`), embeddings computed on your machine |
| SQL Server | Free | Developer edition in Docker, started for you by `run.sh` |

## Detailed Setup Guide

### 1. Prerequisites

| Tool | Check | Install |
|---|---|---|
| Python 3.10 – 3.12 (**not** 3.13 — the pinned dependencies have no 3.13 wheels) | `python3.11 --version` | [python.org](https://www.python.org/downloads/) · macOS: `brew install python@3.11` |
| ODBC Driver 18 for SQL Server | `odbcinst -q -d` | macOS: `brew tap microsoft/mssql-release && brew trust microsoft/mssql-release && HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18 mssql-tools18` · [other platforms](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) |
| Docker | `docker ps` | [Docker Desktop](https://www.docker.com/products/docker-desktop/) |
| Groq API key | – | [console.groq.com/keys](https://console.groq.com/keys) — free |

### 2. Configure

```bash
cp .env.example .env
```

| Variable | What it is | Required? |
|---|---|---|
| `GROQ_API_KEY` | your key from [console.groq.com/keys](https://console.groq.com/keys) | Yes |
| `LLM_MODEL` | any Groq chat model. Default `openai/gpt-oss-120b`; `qwen/qwen3.6-27b` also works | default OK |
| `LLM_BASE_URL` | Groq's endpoint | default OK |
| `DB_SERVER` / `DB_PORT` / `DB_NAME` / `DB_USER` | connection details for the warehouse | defaults OK |
| `DB_PASSWORD` | SQL Server rejects weak passwords: at least 8 characters mixing upper case, lower case, digits and symbols | Yes |

`.env` is gitignored; `.env.example` is the template that ships in the repo.

### 3. What `run.sh` does for you

Run by hand if you want to see the steps:

```bash
source venv/bin/activate

# the database
docker run -d --name mssql -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD="$DB_PASSWORD" \
  -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest
docker exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$DB_PASSWORD" -C \
  -Q "CREATE DATABASE [$DB_NAME]"

# schema + synthetic data: 8 tables, 5 views, 5 stored procedures, ~15,000 rows
cd CreateDataWarehouse && python InsertToSQL.py && cd ..     # --large for ~1M+ rows

# retrieval index: schema plan + DDL + documentation into ChromaDB (no LLM calls, no cost)
cd RAGToSQL && python TrainRAG.py && cd ..

# ask
cd RAGToSQL && python app.py            # the web app on :8085
python AskRAG.py                        # or one question in the terminal
python InferenceRAG.py                  # or generate SQL without running it
cd .. && python LangChainSQL/ask.py "Which 3 advisors manage the most high-risk clients?"
```

Verify the data landed:

```bash
docker exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P "$DB_PASSWORD" -C -d "$DB_NAME" \
  -Q "SELECT COUNT(*) FROM Clients; SELECT TOP 3 * FROM ClientPortfolioValue;"
```

### Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No matching distribution found for onnxruntime` on install | venv built with Python 3.13 | `rm -rf venv && python3.11 -m venv venv`, then `./run.sh` |
| Container exits immediately; `docker logs mssql` says *Password validation failed* | `DB_PASSWORD` too weak | pick one with upper + lower + digit + symbol, `docker rm -f mssql`, run again |
| `Can't open lib 'ODBC Driver 18 for SQL Server'` | driver not installed | see Prerequisites |
| `Login timeout expired` right after starting the container | SQL Server needs ~20 s to accept connections | wait and retry — `run.sh` polls for you |
| `openai.AuthenticationError: 401` | `GROQ_API_KEY` missing or wrong | check `.env`, no quotes around the value |
| `Rate limit reached … tokens per day` | Groq free-tier budget for that model is used up | switch `LLM_MODEL`, or wait — evaluation runs checkpoint and resume |
| `Invalid object name 'Clients'` | database not seeded | `./run.sh --reset` |
| `Port 8085 is in use` | an older server is still running | `lsof -i :8085` then `kill <pid>`, or set `PORT` in `.env` |

## The web app

`RAGToSQL/app.py` (Flask + one HTML page, no extra dependencies) is the interface I built for this
project. Vanna ships its own Flask UI and it works, but it hides the parts that matter here and spends
**four LLM calls per question** (SQL, chart code, prose summary, follow-up questions). This one spends
**one**, and puts the pipeline on screen:

| Panel | What it shows | Why |
|---|---|---|
| **Schema** | the 8 tables and 5 views with their columns, read live from `INFORMATION_SCHEMA`; click a name to drop it into your question | someone who has never seen this warehouse can still use the demo |
| **Retrieved context** | every chunk ChromaDB returned for the question, with its similarity score, and a green **used** badge on the ones the generated query actually touches (`15 chunks retrieved · 1 used by the query`) | makes the RAG step visible instead of magic - and shows how much of the retrieved context was actually needed. The counts come from Vanna's own `n_results_*` settings, so the panel is exactly what the model was sent |
| **Generated SQL** | the query, pretty-printed with `sqlglot` and **editable**, with the guard's verdict next to it (`single SELECT · no writes · TOP 1000 injected`) | nothing runs until you press Run, so a wrong query is a starting point rather than an accident |
| **Prompt sent to the model** | the exact message list captured inside `submit_prompt` - not a reconstruction - with its size in tokens | closes the loop: retrieved chunks → how they were assembled → what came back |
| **Result** | row count, timing, table, CSV download | - |
| **Chart** | a bar chart drawn in CSS | the chart type is picked **in code** from the shape of the result (a label column + a value column, identifiers excluded). Vanna asks the LLM to write chart code, which for a top-N list happily plots the total against `ClientID` - a meaningless scatter |

Timing is broken out per stage (`retrieval 599ms · model 1.1s · 4,096 tokens · 1 LLM call`), and the last six
questions stay as clickable chips.

```bash
./run.sh              # the app on http://localhost:8085
./run.sh --vanna-ui   # Vanna's own UI on :8084, for comparison
```

## Evaluation

`eval/questions.json` holds 30 natural-language questions with hand-written reference SQL, covering simple counts, GROUP BY, TOP-N, multi-table JOINs, date filters, views and projections. `eval/run_eval.py` asks each question to both pipelines, runs the generated SQL through the read-only guard, executes it, and marks it correct when the returned rows contain the same values as the reference rows (column names/order ignored, numbers rounded to 2 dp). Results: [eval/results.md](eval/results.md).

A side-by-side walkthrough of both pipelines on the same questions - including every query the zero-shot
chain got wrong and why - is in [demo.md](demo.md).

```bash
./run.sh --eval        # Vanna (RAG) vs LangChain (zero-shot)
./run.sh --ablation    # Vanna only: which training data matters? -> eval/ablation.md
```

| System | Correct | Accuracy |
|---|---|---|
| **Vanna (RAG)** | 30/30 | **100.0%** |
| LangChain (zero-shot) | 25/30 | 83.3% |

Model: Groq `qwen/qwen3.6-27b`, same model and same questions for both systems.

**On leakage.** ChromaDB holds no question/SQL pairs at all - only DDL, the `INFORMATION_SCHEMA` plan and
`Documentation.txt` - so no question's answer was memorised. But `Documentation.txt` ends with a *"typical
questions and where to look"* section that routes common question shapes to the right view, and four of the
thirty questions (1, 3, 17, 30) match one of those lines closely enough that the hint is effectively the
answer template. Excluding those four:

| System | Correct (26 questions) | Accuracy |
|---|---|---|
| **Vanna (RAG)** | **26/26** | **100%** |
| LangChain (zero-shot) | 23/26 | 88% |

Two of the four excluded questions were zero-shot failures, so dropping them narrows the gap - and makes
the remaining story cleaner: every surviving failure (20, 21, 29) is a business-rule error on a question no
hint routes. That is where the documentation earns its keep.

All five zero-shot failures are **semantic, not syntactic** - the chain sees the schema but not the
business rules the documentation encodes:

| # | Question | What the zero-shot chain did |
|---|---|---|
| 1 | top clients by portfolio value | recomputed the metric by hand instead of using the `ClientPortfolioValue` view, and grouped it differently |
| 17 | top portfolios by value | `SUM(Allocation * CurrentValue)` - forgot that `Allocation` is a **percentage** (`/ 100`) |
| 20 | average portfolio value per risk level | averaged `Projections.FutureValue` - a projection, not a current value |
| 21 | Low-risk clients holding a High-risk portfolio | `COUNT(*)` instead of `COUNT(DISTINCT ClientID)`, double-counting clients with several portfolios |
| 29 | portfolios with more than 5 assets | counted `PortfolioAssets` rows instead of distinct assets |

That is the case for retrieval-augmented grounding in one table: both systems get the same schema, but
only the RAG path is told what "portfolio value" means, that allocations are percentages, and which view
already answers which question.

The ablation retrains Vanna into a scratch ChromaDB four times - (A) DDL only, (B) + `INFORMATION_SCHEMA` plan, (C) + `Documentation.txt` (= production), (D) + 5 held-out question/SQL examples - and re-asks the same questions each time, to show which training source actually earns its place.

The subset is deliberately weighted towards the questions where the schema alone is not enough - a subset of
easy questions scores full marks in every configuration and measures nothing.

| Configuration | Correct (12 questions) | What it adds |
|---|---|---|
| A · DDL only | 10/12 | table, view and procedure definitions |
| B · + `INFORMATION_SCHEMA` plan | 9/12 | an auto-generated column listing per table |
| **C · + `Documentation.txt`** (production) | **12/12** | business rules, allowed values, view guidance |
| D · + 5 question/SQL examples | 11/12 | five worked examples, held out of the eval set |

Two findings, both actionable:

- **The documentation is the decisive ingredient.** A and B fail the same two questions — the average
  portfolio value per risk level (they average `Projections.FutureValue`, a forecast, instead of the current
  value) and the count of low-risk clients holding a high-risk portfolio (`COUNT(*)` instead of
  `COUNT(DISTINCT ClientID)`). Neither mistake is visible in the schema; both are spelled out in the
  documentation, and C gets both right.
- **The auto-generated schema plan does not pay for itself.** It occupies roughly half of every prompt and
  scores no better than plain DDL — slightly worse here, since it also lost question 4. It restates in a
  markdown table what the `CREATE TABLE` statements already say. Dropping it would roughly halve the tokens
  spent per question.

Adding worked examples (D) did not help either: five examples unrelated to the failing question pulled the
model off question 21, which C answers correctly. Full run: [eval/ablation.md](eval/ablation.md).

## Making reasoning models usable

Groq's `gpt-oss` / `qwen` models are reasoning models: they think out loud before answering, and their
chain-of-thought is full of half-written SQL. Two things were needed to make them work as SQL generators:

- **`reasoning_effort: "low"`** on every call. Without it the models spend their whole completion budget
  reasoning and return an empty answer (`MyVanna.submit_prompt` is overridden because Vanna's own
  implementation cannot pass provider-specific options).
- **A parser-validated SQL extractor** (`RAGToSQL/Helper/SqlExtract.py`). Vanna's built-in `extract_sql`
  matches `SELECT.*?;` first, so on a reasoning model it returns prose glued to a query. The replacement
  strips `<think>` blocks, enumerates every candidate (fenced code blocks first, then each `SELECT`/`WITH`
  position, latest first) and returns **the first candidate that actually parses as a single SELECT** -
  parsing is the acceptance test, so reasoning text can never be mistaken for SQL. Re-running it over the
  malformed responses collected in an earlier evaluation run recovered 17 of 19.

## Read-only SQL guard

Every generated query passes through `RAGToSQL/Helper/SqlGuard.py` before it touches the database (`guard(sql)`, wired into Vanna via `make_run_sql` and into the LangChain script):

- parsed with `sqlglot` (T-SQL dialect) - unparseable output is rejected instead of executed
- exactly one statement; must be a `SELECT` (CTEs and UNIONs allowed)
- the AST is walked and any `INSERT / UPDATE / DELETE / MERGE / DROP / CREATE / ALTER / TRUNCATE / EXEC / GRANT / transaction` node is rejected, as is `SELECT … INTO`
- `TOP 1000` is injected when the query has no `TOP`, so a runaway query cannot dump a whole table

Combined with a read-only database login this makes the "LLM writes SQL, we run it" loop safe to expose to end users.

## Description of Files and Folders

**`CreateDataWarehouse/`**
- `InsertToSQL.py` — creates the schema and seeds it with Faker data (`--large`, `--no-schema`).
- `SQL/create_tables.sql`, `SQL/create_views_sql.sql`, `SQL/stored_procedures.sql` — the DDL.
- `SQL/Tables.json`, `SQL/Views.json`, `SQL/Proc.json` — the same DDL as JSON, used as retrieval training input.

**`RAGToSQL/`** (the RAG path)
- `app.py` + `templates/index.html` — this project's web app (see above).
- `TrainRAG.py` — loads the schema plan, DDL and documentation into ChromaDB.
- `AskRAG.py` — one question, generated and executed, in the terminal.
- `InferenceRAG.py` — SQL generation only, no execution.
- `VisualizeRAG.py` — Vanna's bundled UI, kept for comparison.
- `Helper/Credentials.py` — all configuration, read from `.env`.
- `Helper/Database.py` — opens the warehouse connection.
- `Helper/VannaObject.py` — ChromaDB vector store + Groq chat client, with the prompt captured and the SQL extractor overridden.
- `Helper/SqlGuard.py` — the read-only guard (see above).
- `Helper/SqlExtract.py` — parser-validated extraction of SQL from a reasoning model's answer.
- `TrainingRAG-Artifact/` — the DDL JSON and `Documentation.txt` the index is built from.

**`LangChainSQL/`** (the zero-shot path)
- `chain.py` — `LangChainTextToSQL`, used by `ask.py` and by the evaluation.
- `ask.py` — answer one question from the command line.
- `test_connection.py` — minimal connectivity check.

**`eval/`** — `questions.json` (30 questions + reference SQL), `fewshot_pairs.json` (5 held-out examples for the ablation), `run_eval.py`, and the generated `results.md`.

**`demo.md`** — the two pipelines side by side on the same questions, including every query the zero-shot chain got wrong and why.

**`run.sh`** — one-command launcher. **`requirements.txt`** — dependencies. **`.env.example`** — configuration template. **`LICENSE`** — MIT.

## Features

### Database Features
- **8 tables**: `Advisors`, `Clients`, `Accounts`, `Assets`, `Transactions`, `Portfolios`, `PortfolioAssets`, `Projections` (identity PKs, FK relationships).
- **5 views**: `PortfolioAssetAllocation`, `ClientPortfolioValue`, `PortfolioSummary`, `AccountTransactionHistory`, `OverallWealthSummary`.
- **5 stored procedures**: `CalculateClientRiskAssessment`, `CalculateTotalPortfolioValue`, `IdentifyUnderperformingAssets`, `GetPortfolioPerformanceOverTime`, `AnalyzeAssetAllocation`.
- **Synthetic data** at two scales (~15K rows or ~1M+ rows), generated with Faker - no real client records.

### Text-to-SQL Features
- **Vanna AI (RAG)**: schema, DDL, and documentation are embedded into ChromaDB; every question is answered with the most relevant schema chunks in the prompt, so generated SQL is grounded in the real tables and views.
- **LangChain SQL chain**: zero-setup alternative that introspects the schema at runtime.
- **Measured, not asserted**: 30 held-out questions with reference SQL, scored by execution accuracy; runs checkpoint so a rate-limited run resumes instead of restarting.
- **Read-only by construction**: every generated query is parsed and rejected unless it is a single `SELECT`, with a row cap injected.
- **Web app**: retrieved context, the guard's verdict, the editable SQL, the exact prompt, the result and a chart - one LLM call per question.
- **Groq LLM**: fast, free-tier-friendly, model swappable via `LLM_MODEL`.

## Technical Details

### How a question becomes an answer
1. The question is embedded and ChromaDB returns the closest schema, documentation and example chunks.
2. Those chunks plus the question are assembled into a prompt and sent to Groq (`LLM_MODEL`).
3. The answer is run through the SQL extractor, then the read-only guard.
4. The guarded query is executed with `pandas.read_sql_query`; the result shape decides the chart.

### Why the documentation matters
The DDL says `Allocation DECIMAL(18,2)`. It does not say that the number is a percentage, that a
portfolio's value is `Σ Allocation × CurrentValue / 100`, that "risk" lives in two different columns
depending on whether you mean a client or a portfolio, or that `ClientPortfolioValue` already answers
"top clients by wealth". `Documentation.txt` says all of that, and the evaluation shows it is exactly
where the accuracy difference comes from.

### LLM provider
The project talks to Groq only. Groq implements the OpenAI wire protocol, so the `openai` /
`langchain_openai` packages act purely as the HTTP client with `base_url` pointed at
`https://api.groq.com/openai/v1` — no OpenAI account or key is involved anywhere. Any other Groq model can
be selected with `LLM_MODEL`.

## Example Usage

Questions that work well against the seeded schema:

- *Which 5 clients have the highest total portfolio value?*
- *How many clients are there in each risk profile?*
- *What is the total wealth by asset type?*
- *Which 3 advisors manage the most high-risk clients?*
- *What is the total amount of Buy transactions per account type?*
- *How many clients with a Low risk profile have a High risk portfolio?*

```
Question: Which 3 advisors manage the most high-risk clients?
SQL:      SELECT TOP 3 a.Name, COUNT(*) AS HighRiskClientCount
          FROM Advisors a JOIN Clients c ON c.AdvisorID = a.AdvisorID
          WHERE c.RiskProfile = 'High'
          GROUP BY a.Name ORDER BY HighRiskClientCount DESC, a.Name
Result:   Leslie Dorsey 9 · Adam Graham 7 · Adam Mathews 7
```

See [demo.md](demo.md) for both pipelines answering the same questions side by side.

## Credits

Enes Çağlar
