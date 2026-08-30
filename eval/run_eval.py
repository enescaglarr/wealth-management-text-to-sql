"""
Execution-accuracy evaluation of the two Text-to-SQL paths.

    python eval/run_eval.py                 # Vanna (production training) vs LangChain, 30 questions
    python eval/run_eval.py --ablation      # Vanna only, 4 training configurations
    python eval/run_eval.py --limit 5       # quick smoke run

A prediction is correct when the rows returned by the generated SQL contain the same values as the
rows returned by the reference SQL (column names / order ignored, numbers rounded to 2 dp,
extra columns tolerated). For questions marked compare="first_row_and_values" (ties), only the
top row and the multiset of numeric values must match.
Results are written to eval/results.md (or eval/ablation.md).
"""
import argparse
import warnings
import json
import os
import shutil
import sys
import time
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyodbc
from dotenv import load_dotenv

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]

_ap = argparse.ArgumentParser()
_ap.add_argument("--ablation", action="store_true")
_ap.add_argument("--limit", type=int, default=None, help="only the first N questions")
_ap.add_argument("--step", type=int, default=1, help="use every Nth question (budget-friendly subset)")
_ap.add_argument("--ids", default=None,
                 help="comma-separated question ids, e.g. 1,17,20 - use for the ablation, where a subset that "
                      "contains no hard questions would score full marks in every configuration and prove nothing")
_ap.add_argument("--configs", default="ABCD", help="ablation configs to run, e.g. ACD")
_ap.add_argument("--fresh", action="store_true", help="ignore the checkpoint and re-ask every question")
_ap.add_argument("--report-only", action="store_true", help="rebuild the .md report from the existing .csv")
_ap.add_argument("--model", default=None, help="override LLM_MODEL for this run (Groq free tier has a per-model daily token limit)")
ARGS = _ap.parse_args()
if ARGS.model:
    os.environ["LLM_MODEL"] = ARGS.model
sys.path.insert(0, str(ROOT / "RAGToSQL"))
sys.path.insert(0, str(ROOT / "LangChainSQL"))
load_dotenv(ROOT / ".env")

from Helper.Credentials import Credentials  # noqa: E402
from Helper.Database import get_connection  # noqa: E402
from Helper.SqlGuard import UnsafeSQL, guard, make_run_sql  # noqa: E402
from Helper.VannaObject import MyVanna  # noqa: E402

QUESTIONS = json.load(open(ROOT / "eval" / "questions.json"))
FEWSHOT = json.load(open(ROOT / "eval" / "fewshot_pairs.json"))
ARTIFACT = ROOT / "RAGToSQL" / "TrainingRAG-Artifact"


# ---------------------------------------------------------------- comparison
def _norm(v):
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return round(float(v), 2)
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    sv = str(v).strip()
    try:
        return round(float(sv), 2)  # numeric strings like '2021' compare equal to numbers
    except ValueError:
        return sv.lower()


def _rows(df):
    return [set(_norm(v) for v in r) for r in df.itertuples(index=False)]


def rows_match(ref: pd.DataFrame, got: pd.DataFrame, mode: str = "rows") -> bool:
    if got is None:
        return False
    R, G = _rows(ref), _rows(got)
    if mode == "first_row_and_values":
        # ties: top row must match, and the multiset of the reference's aggregate column (its last column)
        # must appear across the generated rows - which tied entity fills the last slots does not matter
        if not R or not G or not R[0] <= G[0]:
            return False
        agg = [_norm(v) for v in ref.iloc[:, -1]]
        pool = list(G)
        for v in agg:
            for i, g in enumerate(pool):
                if v in g:
                    pool.pop(i)
                    break
            else:
                return False
        return len(G) == len(R)
    if len(R) != len(G):
        return False
    G = list(G)
    for r in R:  # every reference row must be covered by a distinct generated row (superset allowed)
        for i, g in enumerate(G):
            if r <= g:
                G.pop(i)
                break
        else:
            return False
    return True


# ---------------------------------------------------------------- systems
def llm_call(fn, *a, retries=40):
    """Call an LLM-backed function, honouring Groq's per-minute token limit (waits for the 'try again in Xs' hint)."""
    import re as _re
    for i in range(retries):
        try:
            return fn(*a)
        except Exception as e:  # rate limit / transient
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                m = _re.search(r"try again in ([\d.]+)(ms|s)", msg)
                wait = (float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)) if m else 15
                time.sleep(min(max(wait + 1, 3), 120))
                continue
            raise
    raise RuntimeError("gave up after rate-limit retries")


def train_vanna(path: Path, ddl=True, plan=False, doc=False, fewshot=False, conn=None):
    if path.exists():
        shutil.rmtree(path)
    vn = MyVanna(config={"path": str(path)})
    if ddl:
        for f in ("Tables.json", "Views.json", "Proc.json"):
            for q in json.load(open(ARTIFACT / f)):
                vn.train(ddl=q["command"])
    if plan:
        df = pd.read_sql_query("SELECT * FROM INFORMATION_SCHEMA.COLUMNS", conn)
        vn.train(plan=vn.get_training_plan_generic(df))
    if doc:
        vn.train(documentation=open(ARTIFACT / "Documentation.txt").read())
    if fewshot:
        for p in FEWSHOT:
            vn.train(question=p["question"], sql=p["sql"])
    return vn


def make_vanna_fn(vn, conn):
    # same settings as the production app: guarded run_sql + the LLM may issue an intermediate
    # query to look up distinct column values
    vn.run_sql = make_run_sql(conn)
    vn.run_sql_is_set = True
    return lambda q: llm_call(lambda x: vn.generate_sql(x, allow_llm_to_see_data=True), q)


def make_langchain_fn():
    from chain import LangChainTextToSQL
    t2s = LangChainTextToSQL()
    return lambda q: llm_call(t2s.generate_sql, q)


# ---------------------------------------------------------------- run
def _ckpt_path(ablation):
    # the model is part of the name: results from different models must never be mixed in one report
    # model + question-set hash: results from a different model or from older reference SQL
    # must never be reused in a report
    import hashlib
    slug = Credentials.model.replace("/", "-")
    qhash = hashlib.sha1(json.dumps(QUESTIONS, sort_keys=True).encode()).hexdigest()[:8]
    return ROOT / "eval" / f".{'ablation' if ablation else 'results'}.{slug}.{qhash}.jsonl"


def load_checkpoint(path):
    """Rate limits kill long runs; every answered question is appended to a JSONL checkpoint so a
    re-run picks up where it stopped instead of paying for the same questions twice."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def evaluate(name, gen_fn, conn, questions, log, ckpt=None, done=()):
    rows = []
    done_ids = {r["id"] for r in done if r["system"] == name}
    for q in questions:
        if q["id"] in done_ids:
            cached = next(r for r in done if r["system"] == name and r["id"] == q["id"])
            rows.append(cached)
            log(f"[{name}] Q{q['id']:02d} {'OK ' if cached['correct'] else 'BAD'} (cached)")
            continue
        t0 = time.time()
        sql, err, got = "", "", None
        try:
            sql = gen_fn(q["question"])
            sql = guard(sql)
            got = pd.read_sql_query(sql, conn)
        except UnsafeSQL as e:
            err = f"guard: {e}"
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:120]}"
        latency = time.time() - t0
        ref = pd.read_sql_query(q["sql"], conn)
        ok = not err and rows_match(ref, got, q.get("compare", "rows"))
        row = {"id": q["id"], "tag": q["tag"], "question": q["question"], "system": name,
               "correct": bool(ok), "latency": latency, "sql": sql, "error": err}
        rows.append(row)
        if ckpt and "rate-limit" not in err:
            with open(ckpt, "a") as fh:
                fh.write(json.dumps(row) + "\n")
        log(f"[{name}] Q{q['id']:02d} {'OK ' if ok else 'ERR' if err else 'BAD'} {latency:4.1f}s  {q['question'][:60]}{'  <- ' + err if err else ''}")
    return rows


def summary_table(results):
    df = pd.DataFrame(results)
    g = df.groupby("system").agg(correct=("correct", "sum"), n=("correct", "size"), latency=("latency", "mean"))
    g["accuracy"] = (100 * g["correct"] / g["n"]).round(1)
    return g


def write_report(path, title, results, systems_desc):
    df = pd.DataFrame(results)
    df["sql"] = df["sql"].fillna("").astype(str)      # a rebuilt report reads NaN back from the csv
    df["error"] = df["error"].fillna("").astype(str)
    s = summary_table(results)
    out = [f"# {title}", "", f"Model: Groq `{Credentials.model}` · {df['id'].nunique()} questions from `eval/questions.json` · metric: execution accuracy (result rows match the reference SQL's rows).", ""]
    out += ["| System | Correct | Accuracy | Avg latency |", "|---|---|---|---|"]
    for name, r in s.iterrows():
        out.append(f"| {name} | {int(r.correct)}/{int(r.n)} | **{r.accuracy}%** | {r.latency:.1f}s |")
    out += ["", "> Latency is wall-clock per question and **includes waiting on Groq's free-tier rate limit**",
            "> (8,000 tokens/minute), which dominates it - it is not a measure of model speed.", ""]
    out += ["Systems:", ""] + [f"- **{k}** - {v}" for k, v in systems_desc.items()] + [""]
    piv = df.pivot_table(index=["id", "tag", "question"], columns="system", values="correct", aggfunc="first").reset_index()
    out += ["## Per question", ""]
    cols = [c for c in piv.columns if c not in ("id", "tag", "question")]
    out += ["| # | tag | question | " + " | ".join(cols) + " |", "|---|---|---|" + "---|" * len(cols)]
    for _, r in piv.iterrows():
        out.append(f"| {r['id']} | {r['tag']} | {r['question']} | " + " | ".join("✅" if r[c] else "❌" for c in cols) + " |")
    out += ["", "## By question type", ""]
    bt = df.pivot_table(index="tag", columns="system", values="correct", aggfunc="mean").mul(100).round(0)
    out += ["| tag | " + " | ".join(bt.columns) + " |", "|---|" + "---|" * len(bt.columns)]
    for tag, r in bt.iterrows():
        out.append(f"| {tag} | " + " | ".join(f"{int(v)}%" for v in r) + " |")
    fails = df[~df.correct]
    if len(fails):
        out += ["", "## Failures", ""]
        for _, r in fails.iterrows():
            out += [f"**Q{r['id']} · {r['system']}** - {r['question']}", "", "```sql", r["sql"] or "(no SQL)", "```", (f"Error: `{r['error']}`" if r["error"] else "Returned rows differ from the reference."), ""]
    Path(path).write_text("\n".join(out))
    df.to_csv(str(path).replace(".md", ".csv"), index=False)


REPORTS = {
    False: (ROOT / "eval" / "results.md", "Text-to-SQL evaluation: Vanna (RAG) vs LangChain (zero-shot)",
            {"Vanna (RAG)": "schema DDL + INFORMATION_SCHEMA plan + Documentation.txt embedded in ChromaDB; relevant chunks retrieved per question",
             "LangChain (zero-shot)": "`create_sql_query_chain` with the live schema (`SQLDatabase`) in the prompt, no training / documentation"}),
    True: (ROOT / "eval" / "ablation.md", "Ablation: what Vanna training data matters?",
           {"A": "table/view/procedure DDL only", "B": "A + auto-generated INFORMATION_SCHEMA training plan",
            "C": "B + hand-written Documentation.txt (= production setup)", "D": "C + 5 question/SQL examples (held out from the eval set)"}),
}


def main():
    args = ARGS
    if args.report_only:
        path, title, desc = REPORTS[args.ablation]
        rows = pd.read_csv(str(path).replace(".md", ".csv")).to_dict("records")
        write_report(path, title, rows, desc)
        print(f"rebuilt {path}")
        return
    if args.ids:
        wanted = [int(i) for i in args.ids.split(",")]
        questions = [q for q in QUESTIONS if q["id"] in wanted]
    else:
        questions = QUESTIONS[: args.limit] if args.limit else QUESTIONS
        questions = questions[:: args.step]
    conn = get_connection()
    log = lambda m: print(m, flush=True)
    results = []
    tmp = ROOT / "eval" / ".chroma"
    ckpt = _ckpt_path(args.ablation)
    if args.fresh:
        ckpt.unlink(missing_ok=True)
    done = load_checkpoint(ckpt)
    if done:
        log(f"(resuming: {len(done)} answers already in {ckpt.name})")

    if args.ablation:
        configs = {
            "A: DDL only":               dict(ddl=True),
            "B: DDL + schema plan":      dict(ddl=True, plan=True),
            "C: DDL + plan + docs":      dict(ddl=True, plan=True, doc=True),
            "D: C + 5 few-shot pairs":   dict(ddl=True, plan=True, doc=True, fewshot=True),
        }
        for name, cfg in configs.items():
            if name[0] not in args.configs.upper():
                continue
            if {q["id"] for q in questions} <= {r["id"] for r in done if r["system"] == name}:
                log(f"=== {name}: all cached")
                results += evaluate(name, None, conn, questions, log, ckpt, done)
                continue
            log(f"\n=== training {name}")
            vn = train_vanna(tmp / name[0], conn=conn, **cfg)
            results += evaluate(name, make_vanna_fn(vn, conn), conn, questions, log, ckpt, done)
        write_report(ROOT / "eval" / "ablation.md", "Ablation: what Vanna training data matters?", results,
                     {"A": "table/view/procedure DDL only", "B": "A + auto-generated INFORMATION_SCHEMA training plan",
                      "C": "B + hand-written Documentation.txt (= production setup)", "D": "C + 5 question/SQL examples (held out from the eval set)"})
    else:
        log("=== Vanna (production ChromaDB)")
        vn = MyVanna()
        results += evaluate("Vanna (RAG)", make_vanna_fn(vn, conn), conn, questions, log, ckpt, done)
        log("=== LangChain")
        results += evaluate("LangChain (zero-shot)", make_langchain_fn(), conn, questions, log, ckpt, done)
        write_report(ROOT / "eval" / "results.md", "Text-to-SQL evaluation: Vanna (RAG) vs LangChain (zero-shot)", results,
                     {"Vanna (RAG)": "schema DDL + INFORMATION_SCHEMA plan + Documentation.txt embedded in ChromaDB; relevant chunks retrieved per question",
                      "LangChain (zero-shot)": "`create_sql_query_chain` with the live schema (`SQLDatabase`) in the prompt, no training / documentation"})
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + summary_table(results).to_string())


if __name__ == "__main__":
    main()
