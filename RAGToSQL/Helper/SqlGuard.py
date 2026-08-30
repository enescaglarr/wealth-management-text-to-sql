"""
Read-only guard for LLM-generated SQL.

    guard(sql) -> sanitized sql   (raises UnsafeSQL otherwise)

Rules
- exactly one statement
- must be a SELECT (CTEs / UNIONs allowed)
- no INSERT/UPDATE/DELETE/MERGE/DROP/CREATE/ALTER/TRUNCATE/EXEC/GRANT/… anywhere in the tree
- no SELECT … INTO
- if the query has no TOP, `TOP {max_rows}` is injected so a bad query cannot dump the table
"""
import re

import sqlglot
from sqlglot import exp

FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Transaction, exp.Commit, exp.Rollback, exp.Grant,
)
FORBIDDEN_WORDS = ("exec", "execute", "sp_", "xp_", "openrowset", "opendatasource", "bulk", "waitfor", "shutdown")
_WORD_RE = re.compile(r"\b(" + "|".join(w.rstrip("_") for w in FORBIDDEN_WORDS) + r")\b", re.I)


class UnsafeSQL(Exception):
    pass


def guard(sql: str, max_rows: int = 1000, dialect: str = "tsql") -> str:
    if not sql or not sql.strip():
        raise UnsafeSQL("empty SQL")
    m = _WORD_RE.search(sql)
    if m:
        raise UnsafeSQL(f"forbidden keyword: {m.group(0)}")
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.SqlglotError as e:  # ParseError, TokenError, …
        raise UnsafeSQL(f"could not parse SQL: {str(e).splitlines()[0][:120]}") from e
    if len(statements) != 1:
        raise UnsafeSQL(f"expected exactly 1 statement, got {len(statements)}")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise UnsafeSQL(f"only SELECT statements are allowed, got {type(tree).__name__}")
    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise UnsafeSQL(f"forbidden operation: {type(node).__name__}")
    if tree.args.get("into"):
        raise UnsafeSQL("SELECT INTO is not allowed")
    if isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree = tree.limit(max_rows)
    return tree.sql(dialect=dialect)


def make_run_sql(conn, max_rows: int = 1000):
    """Return a Vanna-compatible run_sql(sql) that guards every query before executing it."""
    import pandas as pd

    def run_sql(sql: str):
        return pd.read_sql_query(guard(sql, max_rows=max_rows), conn)

    return run_sql
