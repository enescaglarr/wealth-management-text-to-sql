"""Ask one question through the LangChain path (no training step).

    python ask.py "How many clients are there in each risk profile?"
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "RAGToSQL"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from Helper.SqlGuard import guard  # noqa: E402
from chain import LangChainTextToSQL  # noqa: E402

question = sys.argv[1] if len(sys.argv) > 1 else "What are the top 3 clients with max portfolio value?"

t2s = LangChainTextToSQL()
sql = t2s.generate_sql(question)
if not sql:
    raise SystemExit("LLM returned no SQL after 3 attempts")
sql = guard(sql)  # read-only: SELECT only, single statement, TOP injected

print("Question:", question)
print("SQL:", sql)
print("Result:")
print(pd.read_sql_query(sql, t2s.engine).to_string(index=False))
