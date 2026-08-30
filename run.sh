#!/usr/bin/env bash
# One-command launcher: starts (or first-time creates) everything and opens the web app.
#   ./run.sh                       -> start SQL Server, seed/train if needed, open http://localhost:8085
#   ./run.sh --reset               -> wipe the database container + Chroma and rebuild from scratch
#   ./run.sh --train               -> retrain Vanna (after editing Documentation.txt / DDL) then start
#   ./run.sh --ask "your question" -> run the LangChain path once instead of the web app
#   ./run.sh --eval                -> run the 30-question evaluation      -> eval/results.md
#   ./run.sh --ablation            -> run the training-data ablation      -> eval/ablation.md
#   ./run.sh --vanna-ui            -> Vanna's bundled UI on :8084 instead of this project's app
set -euo pipefail
cd "$(dirname "$0")"

PORT=${PORT:-8085}
info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

RESET=0; TRAIN=0; ASK=0; EVAL=0; ABLATION=0; VANNA_UI=0
for a in "$@"; do case "$a" in
  --reset) RESET=1;; --train) TRAIN=1;; --ask) ASK=1;;
  --eval) EVAL=1;; --ablation) ABLATION=1;; --vanna-ui) VANNA_UI=1;;
esac; done
[ "$VANNA_UI" = 1 ] && PORT=8084   # Vanna's bundled UI listens on 8084

# --- 1. venv
if [ ! -x venv/bin/python ]; then
  PY=$(command -v python3.11 || command -v python3.12 || command -v python3.10 || true)
  [ -n "$PY" ] || die "Python 3.10-3.12 not found (brew install python@3.11)"
  info "Creating venv with $PY"; "$PY" -m venv venv
  venv/bin/pip install -q --upgrade pip; venv/bin/pip install -q -r requirements.txt
fi
# shellcheck disable=SC1091
source venv/bin/activate

# --- 2. .env
[ -f .env ] || { cp .env.example .env; die ".env created from .env.example - fill in GROQ_API_KEY and DB_PASSWORD, then run again"; }
set -a; source .env; set +a
[ -n "${GROQ_API_KEY:-}" ] && [ "$GROQ_API_KEY" != "gsk_..." ] || die "GROQ_API_KEY is not set in .env"
[ -n "${DB_PASSWORD:-}" ] && [ "$DB_PASSWORD" != "your-password" ] || die "DB_PASSWORD is not set in .env"

# --- 3. database
command -v docker >/dev/null || die "Docker not found - install Docker Desktop"
docker info >/dev/null 2>&1 || die "Docker is not running - open Docker Desktop and retry"

if [ "$RESET" = 1 ]; then info "Removing SQL Server container"; docker rm -f mssql >/dev/null 2>&1 || true; fi

FRESH_DB=0
if ! docker container inspect mssql >/dev/null 2>&1; then
  info "Creating SQL Server container (first run)"
  docker run -d --name mssql -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD="$DB_PASSWORD" \
    -p 1433:1433 mcr.microsoft.com/mssql/server:2022-latest >/dev/null
  FRESH_DB=1
elif [ "$(docker inspect -f '{{.State.Running}}' mssql)" != "true" ]; then
  info "Starting SQL Server container"; docker start mssql >/dev/null
fi

info "Waiting for SQL Server"
for i in $(seq 1 40); do
  if docker exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U "$DB_USER" -P "$DB_PASSWORD" -C -Q "SELECT 1" >/dev/null 2>&1; then break; fi
  [ "$i" = 40 ] && { docker logs mssql | tail -5; die "SQL Server did not come up (weak password? see docker logs mssql)"; }
  sleep 2
done

docker exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U "$DB_USER" -P "$DB_PASSWORD" -C \
  -Q "IF DB_ID('$DB_NAME') IS NULL CREATE DATABASE [$DB_NAME];" >/dev/null

HAS_TABLES=$(docker exec mssql /opt/mssql-tools18/bin/sqlcmd -S localhost -U "$DB_USER" -P "$DB_PASSWORD" -C -d "$DB_NAME" -h -1 -W \
  -Q "SET NOCOUNT ON; SELECT COUNT(*) FROM sys.tables WHERE name='Clients'" | tr -d '[:space:]')
if [ "$FRESH_DB" = 1 ] || [ "$HAS_TABLES" = "0" ]; then
  info "Seeding schema + synthetic data"; (cd CreateDataWarehouse && python InsertToSQL.py)
  TRAIN=1
fi

# --- 4. Vanna training
if [ "$RESET" = 1 ] || [ "$TRAIN" = 1 ] || [ ! -f RAGToSQL/chroma.sqlite3 ]; then
  info "Training Vanna on the schema"
  rm -f RAGToSQL/chroma.sqlite3; find RAGToSQL -maxdepth 1 -type d -name '????????-????-????-????-????????????' -exec rm -rf {} + 2>/dev/null || true
  (cd RAGToSQL && python TrainRAG.py | grep -i "successfull")
fi

# --- 5. run
rest=(); for a in "$@"; do case "$a" in --ask|--eval|--ablation|--reset|--train|--vanna-ui) ;; *) rest+=("$a");; esac; done
if [ "$EVAL" = 1 ]; then exec python eval/run_eval.py "${rest[@]+"${rest[@]}"}"; fi
if [ "$ABLATION" = 1 ]; then exec python eval/run_eval.py --ablation "${rest[@]+"${rest[@]}"}"; fi
if [ "$ASK" = 1 ]; then exec python LangChainSQL/ask.py "${rest[@]+"${rest[@]}"}"; fi

if lsof -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  info "Port $PORT already in use - stopping the old server"; lsof -tiTCP:$PORT -sTCP:LISTEN | xargs kill 2>/dev/null || true; sleep 1
fi
if [ "$VANNA_UI" = 1 ]; then info "Starting Vanna's bundled UI at http://localhost:$PORT  (Ctrl+C to stop)"
else info "Starting the web app at http://localhost:$PORT  (Ctrl+C to stop)"; fi
( for i in $(seq 1 30); do curl -s -o /dev/null "http://localhost:$PORT" && break; sleep 1; done
  if command -v open >/dev/null; then open "http://localhost:$PORT"; elif command -v xdg-open >/dev/null; then xdg-open "http://localhost:$PORT"; fi ) &
cd RAGToSQL
if [ "$VANNA_UI" = 1 ]; then exec python VisualizeRAG.py; fi
exec python app.py
