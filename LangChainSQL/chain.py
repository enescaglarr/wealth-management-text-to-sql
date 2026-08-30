"""The LangChain Text-to-SQL path: the schema is read live from the database, no training step.

Used by ask.py and by eval/run_eval.py."""
import os
import sys
from pathlib import Path

import sqlalchemy as sa
from dotenv import load_dotenv
from langchain.chains import create_sql_query_chain
from langchain_community.utilities import SQLDatabase
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "RAGToSQL"))
from Helper.Credentials import Credentials  # noqa: E402
from Helper.SqlExtract import extract_sql  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def get_engine():
    import urllib.parse
    return sa.create_engine("mssql+pyodbc:///?odbc_connect=" + urllib.parse.quote(Credentials.connection_string()))


def get_llm():
    return ChatOpenAI(
        model=Credentials.model,
        api_key=Credentials.llm_api_key,
        base_url=Credentials.llm_base_url,
        temperature=0,
        extra_body=Credentials.extra_body(),  # keep reasoning short so the SQL answer is not cut off
    )


class LangChainTextToSQL:
    def __init__(self):
        self.engine = get_engine()
        self.db = SQLDatabase(self.engine)
        self.chain = create_sql_query_chain(get_llm(), self.db)

    def generate_sql(self, question: str, attempts: int = 3) -> str:
        for _ in range(attempts):  # reasoning models occasionally return an empty completion - retry
            sql = extract_sql(self.chain.invoke({"question": question}))
            if sql:
                return sql
        return ""
