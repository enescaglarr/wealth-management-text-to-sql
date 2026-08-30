"""
Wealth Management Text-to-SQL - web app.

Unlike Vanna's bundled Flask app this one makes the pipeline visible rather than hiding it:
the retrieved context, the guard's verdict and the generated SQL are all shown, the SQL stays
editable before it runs, and the chart type is chosen in code. It also costs one LLM call per
question instead of four (Vanna's UI additionally calls the model for the chart code, a prose
summary and follow-up questions).

    python app.py            # http://localhost:8085
"""
import json
import os
import re
import threading
import time

import pandas as pd
import sqlglot
from flask import Flask, jsonify, render_template, request
from sqlglot import exp

from Helper.Credentials import Credentials
from Helper.Database import get_connection
from Helper.SqlGuard import UnsafeSQL, guard
from Helper.VannaObject import MyVanna

EXAMPLES = [
    "Which 5 clients have the highest total portfolio value?",
    "How many clients are there in each risk profile?",
    "What is the total wealth by asset type?",
    "Which 3 advisors manage the most high-risk clients?",
    "What is the total amount of Buy transactions per account type?",
    "What is the total transaction amount per year?",
]

app = Flask(__name__)
vn = MyVanna()
_conn = None
_db_lock = threading.Lock()  # one pyodbc connection, Flask serves requests on threads


def db():
    global _conn
    if _conn is None:
        _conn = get_connection()
    return _conn


def _short(text: str, limit: int = 320) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit] + " …"


def _label(ddl: str) -> str:
    upper = ddl.upper()
    for kind in ("VIEW", "PROCEDURE", "TABLE"):
        marker = f"CREATE {kind} "
        at = upper.find(marker)
        if at != -1:
            after = ddl[at + len(marker):]  # slice the original so the name keeps its casing
            name = after.split("(")[0].split(" AS")[0].strip().strip("[]")
            return f"{kind.title()} · {name.split()[0]}"
    return "DDL"


def _similarity(distance: float) -> int:
    """ChromaDB returns L2 distance over normalised embeddings; d² = 2 - 2·cos."""
    return max(0, min(100, round(100 * (1 - (distance ** 2) / 2))))


def _query(collection, question: str, n: int) -> list:
    try:
        res = collection.query(query_texts=[question], n_results=n, include=["documents", "distances"])
    except Exception:
        return []
    return list(zip(res["documents"][0], res["distances"][0]))


def retrieved_context(question: str) -> list:
    """Everything the RAG layer pulled from ChromaDB, with its similarity score - the panel shows
    the top few and can expand to the rest."""
    # take the counts from Vanna itself, so the panel always shows exactly what the model was sent
    items = []
    for doc, dist in _query(vn.ddl_collection, question, vn.n_results_ddl):
        items.append({"kind": "schema", "label": _label(doc), "text": _short(doc),
                      "score": _similarity(dist), "object": _object_name(doc)})
    for doc, dist in _query(vn.documentation_collection, question, vn.n_results_documentation):
        items.append({"kind": "docs", "label": "Documentation.txt", "text": _short(doc),
                      "score": _similarity(dist), "object": None})
    for doc, dist in _query(vn.sql_collection, question, vn.n_results_sql):
        try:
            pair = json.loads(doc)
            label, text = _short(pair.get("question", ""), 70), _short(pair.get("sql", ""))
        except Exception:
            label, text = "example", _short(doc)
        items.append({"kind": "example", "label": label, "text": text, "score": _similarity(dist), "object": None})
    return sorted(items, key=lambda i: -i["score"])


def _object_name(ddl: str) -> str | None:
    label = _label(ddl)
    return label.split("·")[-1].strip().lower() if "·" in label else None


def tables_in(sql: str) -> set:
    """Which schema objects the generated query actually touches."""
    try:
        tree = sqlglot.parse_one(sql, read="tsql")
    except Exception:
        return set()
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name}


def mark_used(context: list, sql: str) -> list:
    used = tables_in(sql)
    for item in context:
        item["used"] = bool(item.get("object") and item["object"] in used)
    return context


def format_sql(sql: str) -> str:
    """One-line SQL is hard to read and hard to edit; pretty-print it for the editor."""
    try:
        pretty = sqlglot.transpile(sql, read="tsql", write="tsql", pretty=True)[0]
    except Exception:
        return sql
    # sqlglot puts TOP on its own line; keep it on the SELECT where it reads naturally
    return re.sub(r"^SELECT\s*\n\s*TOP\s+(\d+)", r"SELECT TOP \1", pretty, flags=re.M)


def schema_overview() -> dict:
    """Tables and views with their columns, straight from INFORMATION_SCHEMA."""
    sql = """
        SELECT t.TABLE_NAME, t.TABLE_TYPE, c.COLUMN_NAME, c.DATA_TYPE, c.ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.TABLES t
        JOIN INFORMATION_SCHEMA.COLUMNS c ON c.TABLE_NAME = t.TABLE_NAME
        ORDER BY t.TABLE_TYPE, t.TABLE_NAME, c.ORDINAL_POSITION
    """
    with _db_lock:
        df = pd.read_sql_query(sql, db())
    groups = {}
    for row in df.itertuples(index=False):
        entry = groups.setdefault(row.TABLE_NAME, {"name": row.TABLE_NAME,
                                                   "kind": "view" if "VIEW" in row.TABLE_TYPE else "table",
                                                   "columns": []})
        entry["columns"].append({"name": row.COLUMN_NAME, "type": row.DATA_TYPE})
    objects = sorted(groups.values(), key=lambda o: (o["kind"] != "table", o["name"]))
    return {"objects": objects}


def pick_chart(df: pd.DataFrame) -> dict | None:
    """Chart choice is a data question, not a language-model question.

    A bar chart needs a label column and a value column. ID columns are numeric but are not
    values - plotting a total against ClientID (which Vanna's LLM-generated chart code does)
    produces a meaningless scatter, so identifiers are excluded from both roles."""
    if len(df) < 2 or len(df.columns) < 2:
        return None
    is_id = lambda c: str(c).lower().endswith("id")
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not is_id(c)]
    labels = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    if not numeric:
        return None
    if not labels:  # e.g. year + amount: the non-value numeric column becomes the label
        others = [c for c in df.columns if c not in numeric]
        if not others:
            return None
        labels = others[:1]
    label, value = labels[0], numeric[-1]
    rows = df[[label, value]].dropna().head(25)
    values = [float(v) for v in rows[value]]
    top = max(values) or 1
    return {
        "label": str(label),
        "value": str(value),
        "bars": [{"name": str(n), "value": v, "pct": round(100 * v / top, 2)} for n, v in zip(rows[label], values)],
    }


@app.get("/")
def index():
    return render_template("index.html", title="Wealth Management Text-to-SQL")


@app.get("/api/meta")
def meta():
    counts = vn.get_training_data()["training_data_type"].value_counts().to_dict()
    return jsonify({
        "model": Credentials.model,
        "reasoning_effort": Credentials.reasoning_effort() or "default",
        "database": Credentials.database,
        "backend": f"SQL Server · {Credentials.server}",
        "training": counts,
        "examples": EXAMPLES,
    })


@app.get("/api/schema")
def schema():
    try:
        return jsonify(schema_overview())
    except Exception as e:
        return jsonify({"error": str(e)[:200], "objects": []}), 500


@app.post("/api/generate")
def generate():
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "Ask a question first."}), 400
    before = vn.tokens_used
    t0 = time.time()
    context = retrieved_context(question)
    retrieval_ms = round(1000 * (time.time() - t0))
    t1 = time.time()
    try:
        raw_sql = vn.generate_sql(question, allow_llm_to_see_data=True)
    except Exception as e:
        msg = str(e)
        if "rate" in msg.lower() or "429" in msg:
            msg = "Groq rate limit reached - wait a few seconds and try again."
        return jsonify({"error": msg[:300]}), 502
    payload = {
        "question": question,
        "sql": raw_sql,
        "context": mark_used(context, raw_sql),
        "retrieval_ms": retrieval_ms,
        "seconds": round(time.time() - t1, 1),
        "tokens": vn.tokens_used - before,
        "prompt": [{"role": m.get("role", "?"), "content": m.get("content", "")} for m in (vn.last_prompt or [])],
    }
    try:
        payload["guarded_sql"] = format_sql(guard(raw_sql))
        payload["guard"] = {"ok": True, "note": "single SELECT · no writes"
                            + (" · TOP 1000 injected" if "TOP" in payload["guarded_sql"].upper()
                               and "TOP" not in raw_sql.upper() else "")}
    except UnsafeSQL as e:
        payload["guarded_sql"] = ""
        payload["guard"] = {"ok": False, "note": str(e)}
    return jsonify(payload)


@app.post("/api/run")
def run():
    sql = (request.json or {}).get("sql", "").strip()
    try:
        safe = guard(sql)
    except UnsafeSQL as e:
        return jsonify({"error": f"Blocked by the read-only guard: {e}"}), 400
    started = time.time()
    try:
        with _db_lock:
            df = pd.read_sql_query(safe, db())
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {str(e)[:250]}"}), 400
    return jsonify({
        "columns": [str(c) for c in df.columns],
        "rows": df.astype(object).where(pd.notnull(df), None).values.tolist(),
        "row_count": len(df),
        "seconds": round(time.time() - started, 2),
        "chart": pick_chart(df),
        "sql": safe,
    })


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8085"))
    print(f"Wealth Management Text-to-SQL → http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
