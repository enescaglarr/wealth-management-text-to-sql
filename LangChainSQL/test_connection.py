"""Minimal connectivity check: connect and read three rows."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "RAGToSQL"))
from Helper.Database import get_connection  # noqa: E402

connection = get_connection()
cursor = connection.cursor()
cursor.execute("SELECT TOP (3) * FROM [dbo].[Accounts]")
print(cursor.fetchall())
cursor.close()
connection.close()
