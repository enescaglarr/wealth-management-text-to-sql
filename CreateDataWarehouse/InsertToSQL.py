"""
Creates the wealth-management schema (tables, views, stored procedures) and seeds it
with Faker-generated synthetic data.

Usage (from the CreateDataWarehouse/ folder, with .env filled in at the repo root):
    python InsertToSQL.py            # create schema + seed ~15K rows
    python InsertToSQL.py --large    # create schema + seed ~1M+ rows
    python InsertToSQL.py --no-schema  # skip CREATE TABLE/VIEW/PROC, only insert data

"""
import re
import sys
import random
from pathlib import Path
from datetime import datetime, timedelta

import pyodbc
from faker import Faker
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parent / ".env")

sys.path.insert(0, str(ROOT.parent / "RAGToSQL"))
from Helper.Credentials import Credentials  # noqa: E402

LARGE = "--large" in sys.argv
CREATE_SCHEMA = "--no-schema" not in sys.argv

# Row counts: small (default) vs. large run
if LARGE:
    NUM_ADVISORS, NUM_CLIENTS, NUM_ACCOUNTS, NUM_ASSETS = 1_000, 100_000, 200_000, 10_000
    NUM_PORTFOLIOS, NUM_PORTFOLIO_ASSETS, NUM_TRANSACTIONS, NUM_PROJECTIONS = 100_000, 300_000, 500_000, 200_000
else:
    NUM_ADVISORS, NUM_CLIENTS, NUM_ACCOUNTS, NUM_ASSETS = 100, 1_000, 2_000, 1_000
    NUM_PORTFOLIOS, NUM_PORTFOLIO_ASSETS, NUM_TRANSACTIONS, NUM_PROJECTIONS = 1_000, 3_000, 5_000, 2_000

BATCH = 1_000

db = pyodbc.connect(Credentials.connection_string())
cursor = db.cursor()
cursor.fast_executemany = True
fake = Faker()
#pin the seed so the warehouse regenerates identically -- the recorded results in
#demo.md are only re-derivable if every run produces the same synthetic data
fake.seed_instance(20260906)


def run_sql_file(name: str) -> None:
    """Execute a .sql file; CREATE VIEW/PROCEDURE must each be their own batch."""
    sql = (ROOT / "SQL" / name).read_text()
    for batch in re.split(r"(?=CREATE\s+(?:VIEW|PROCEDURE)\b)", sql, flags=re.IGNORECASE):
        if batch.strip():
            cursor.execute(batch)
    db.commit()


def insert_many(label: str, sql: str, rows) -> None:
    rows = list(rows)
    for i in range(0, len(rows), BATCH):
        cursor.executemany(sql, rows[i : i + BATCH])
        db.commit()
    print(f"{label}: {len(rows)} rows")


def max_id(table: str, col: str) -> int:
    return cursor.execute(f"SELECT ISNULL(MAX({col}), 0) FROM {table}").fetchone()[0]


if CREATE_SCHEMA:
    run_sql_file("create_tables.sql")
    print("Tables created")

# Advisors
insert_many("Advisors", "INSERT INTO Advisors (Name, ContactInfo) VALUES (?, ?)",
            ((fake.name(), fake.phone_number()) for _ in range(NUM_ADVISORS)))
advisor_max = max_id("Advisors", "AdvisorID")

# Clients
insert_many("Clients", "INSERT INTO Clients (Name, ContactInfo, AdvisorID, RiskProfile) VALUES (?, ?, ?, ?)",
            ((fake.name(), fake.phone_number(), random.randint(1, advisor_max),
              random.choice(["High", "Medium", "Low"])) for _ in range(NUM_CLIENTS)))
client_max = max_id("Clients", "ClientID")

# Accounts
insert_many("Accounts", "INSERT INTO Accounts (AccountType, ClientID) VALUES (?, ?)",
            ((random.choice(["Savings", "Checking", "Investment"]), random.randint(1, client_max))
             for _ in range(NUM_ACCOUNTS)))
account_max = max_id("Accounts", "AccountID")

# Assets
insert_many("Assets", "INSERT INTO Assets (Name, AssetType, CurrentValue) VALUES (?, ?, ?)",
            ((fake.company(), random.choice(["Stock", "Bond", "Real Estate", "Commodity", "Cash"]),
              round(random.uniform(10, 1000), 2)) for _ in range(NUM_ASSETS)))
asset_max = max_id("Assets", "AssetID")

# Portfolios
insert_many("Portfolios", "INSERT INTO Portfolios (ClientID, Name, RiskLevel) VALUES (?, ?, ?)",
            ((random.randint(1, client_max), f"Portfolio {fake.word()}",
              random.choice(["High", "Medium", "Low"])) for _ in range(NUM_PORTFOLIOS)))
portfolio_max = max_id("Portfolios", "PortfolioID")

# PortfolioAssets
insert_many("PortfolioAssets", "INSERT INTO PortfolioAssets (PortfolioID, AssetID, Allocation) VALUES (?, ?, ?)",
            ((random.randint(1, portfolio_max), random.randint(1, asset_max),
              round(random.uniform(1, 100), 2)) for _ in range(NUM_PORTFOLIO_ASSETS)))

# Transactions
start_date = datetime(2020, 1, 1)
insert_many("Transactions", "INSERT INTO Transactions (AccountID, AssetID, Date, Type, Amount) VALUES (?, ?, ?, ?, ?)",
            ((random.randint(1, account_max), random.randint(1, asset_max),
              start_date + timedelta(days=random.randint(1, 365 * 4)),
              random.choice(["Buy", "Sell", "Deposit", "Withdraw"]),
              round(random.uniform(100, 10000), 2)) for _ in range(NUM_TRANSACTIONS)))

# Projections
insert_many("Projections", "INSERT INTO Projections (PortfolioID, FutureValue, ProjectionDate) VALUES (?, ?, ?)",
            ((random.randint(1, portfolio_max), round(random.uniform(1000, 100000), 2),
              start_date + timedelta(days=random.randint(1, 365 * 10))) for _ in range(NUM_PROJECTIONS)))

if CREATE_SCHEMA:
    run_sql_file("create_views_sql.sql")
    run_sql_file("stored_procedures.sql")
    print("Views and stored procedures created")

cursor.close()
db.close()
print("Done")
