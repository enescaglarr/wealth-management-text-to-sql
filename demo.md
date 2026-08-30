# Demo: RAG grounding vs zero-shot, side by side

Both pipelines answer the same question, against the same warehouse, on the same model - the only
difference is what they are told about the data. The RAG path retrieves the schema DDL **and** the
hand-written `Documentation.txt` (business rules, allowed values, which view answers which question);
the zero-shot LangChain chain sees the live schema and nothing else.

Setup: local SQL Server (Docker) seeded by `InsertToSQL.py` - 100 advisors, 1,000 clients, 2,000 accounts,
1,000 assets, 5,000 transactions, 1,000 portfolios. Model: Groq `qwen/qwen3.6-27b` for both. Every SQL block
below is the verbatim output of `./run.sh --eval`, re-executed against the database to produce the tables.

| System | All 30 questions | Excluding the 4 routed by documentation |
|---|---|---|
| **Vanna (RAG)** | **30/30** · 100% | **26/26** · 100% |
| LangChain (zero-shot) | 25/30 · 83% | 23/26 · 88% |

`Documentation.txt` ends with a section routing common question shapes to the right view, and four questions
(1, 3, 17, 30) match one of those lines closely enough to count as a hint; the second column drops them. Two
of the four were zero-shot failures, so the gap narrows - but every failure that survives is a pure
business-rule error, which is the point.

Full run: [eval/results.md](eval/results.md) - questions and reference SQL: [eval/questions.json](eval/questions.json)

---

## Where both get it right

On questions that map cleanly onto one table or one view, the two are interchangeable.

### What is the total wealth by asset type?

**Result** - both queries return exactly these rows:

| AssetType   |   TotalWealth |
|:------------|--------------:|
| Commodity   |       79337.8 |
| Stock       |      111769   |
| Cash        |       94531.4 |
| Real Estate |      100493   |

_... 1 more rows_

**Vanna (RAG)**

```sql
SELECT TOP 1000 AssetType, TotalWealth FROM OverallWealthSummary
```

**LangChain (zero-shot)**

```sql
SELECT TOP 1000 [AssetType], SUM([CurrentValue]) AS [TotalWealth] FROM [Assets] GROUP BY [AssetType] ORDER BY [TotalWealth] DESC
```

### What is the total amount of Buy transactions per account type?

**Result** - both queries return exactly these rows:

| AccountType   |   TotalBuyAmount |
|:--------------|-----------------:|
| Savings       |      2.02949e+06 |
| Checking      |      2.25642e+06 |
| Investment    |      2.06702e+06 |

**Vanna (RAG)**

```sql
SELECT TOP 1000 AccountType, SUM(Amount) AS TotalBuyAmount FROM AccountTransactionHistory WHERE TransactionType = 'Buy' GROUP BY AccountType
```

**LangChain (zero-shot)**

```sql
SELECT TOP 1000 [a].[AccountType], SUM([t].[Amount]) AS [TotalBuyAmount] FROM [Transactions] AS [t] JOIN [Accounts] AS [a] ON [t].[AccountID] = [a].[AccountID] WHERE [t].[Type] = 'Buy' GROUP BY [a].[AccountType] ORDER BY [TotalBuyAmount] DESC
```

### How many transactions happened in 2022?

**Result** - both queries return exactly these rows:

|   TransactionCount |
|-------------------:|
|               1284 |

**Vanna (RAG)**

```sql
SELECT TOP 1000 COUNT(*) AS TransactionCount FROM Transactions WHERE Date >= '2022-01-01' AND Date < '2023-01-01'
```

**LangChain (zero-shot)**

```sql
SELECT TOP 1000 COUNT(*) AS TransactionCount FROM [Transactions] WHERE YEAR([Date]) = 2022
```

---

## Where the zero-shot chain breaks

All five failures are **semantic, not syntactic**. Every one of these queries runs without error and
returns a plausible-looking table - it is simply the wrong number. That is the failure mode that matters
in a reporting tool, and it is exactly what the retrieved documentation prevents.

### Q1 - Which 5 clients have the highest total portfolio value?

**What went wrong:** `GROUP BY [c].[Name]` - but a name is not a key.

There are two different clients called *Jeffrey Bennett* (ClientID 583 and 631). Grouping by name merges them into a single 5,782 row, which pushes Charles Diaz out of second place. The `ClientPortfolioValue` view groups by `ClientID, Name`, which is why the RAG path gets it right.

**Correct - Vanna (RAG):**

```sql
SELECT TOP 5 ClientName, TotalPortfolioValue FROM ClientPortfolioValue ORDER BY TotalPortfolioValue DESC
```

| ClientName   |   TotalPortfolioValue |
|:-------------|----------------------:|
| Nathan Perry |               5940.47 |
| Charles Diaz |               5573.62 |
| Cynthia Lee  |               4603.81 |

_... 2 more rows_

**Wrong - LangChain (zero-shot):**

```sql
SELECT TOP 5 [c].[Name], SUM([a].[CurrentValue] * ([pa].[Allocation] / 100.0)) AS [TotalPortfolioValue] FROM [Clients] AS [c] JOIN [Portfolios] AS [p] ON [c].[ClientID] = [p].[ClientID] JOIN [PortfolioAssets] AS [pa] ON [p].[PortfolioID] = [pa].[PortfolioID] JOIN [Assets] AS [a] ON [pa].[AssetID] = [a].[AssetID] GROUP BY [c].[Name] ORDER BY [TotalPortfolioValue] DESC
```

| Name            |   TotalPortfolioValue |
|:----------------|----------------------:|
| Nathan Perry    |               5940.47 |
| Jeffrey Bennett |               5782.01 |
| Charles Diaz    |               5573.62 |

_... 2 more rows_

---

### Q17 - Show the 10 portfolios with the highest total value.

**What went wrong:** `SUM(pa.Allocation * a.CurrentValue)` - the `/ 100` is missing.

`Allocation` is a percentage (0-100), so every total comes out exactly 100x too large: 284,731 instead of 2,847.31. The documentation the RAG path retrieves states the rule outright: *value = Allocation / 100.0 x CurrentValue*.

**Correct - Vanna (RAG):**

```sql
SELECT TOP 10 PortfolioID, PortfolioName, TotalPortfolioValue FROM PortfolioSummary ORDER BY TotalPortfolioValue DESC
```

|   PortfolioID | PortfolioName     |   TotalPortfolioValue |
|--------------:|:------------------|----------------------:|
|           443 | Portfolio tonight |               2847.31 |
|           795 | Portfolio dark    |               2829.17 |
|           583 | Portfolio movie   |               2727.66 |

_... 7 more rows_

**Wrong - LangChain (zero-shot):**

```sql
SELECT TOP 10 p.[PortfolioID], p.[Name], SUM(pa.[Allocation] * a.[CurrentValue]) AS TotalValue FROM [Portfolios] AS p JOIN [PortfolioAssets] AS pa ON p.[PortfolioID] = pa.[PortfolioID] JOIN [Assets] AS a ON pa.[AssetID] = a.[AssetID] GROUP BY p.[PortfolioID], p.[Name] ORDER BY TotalValue DESC
```

|   PortfolioID | Name              |   TotalValue |
|--------------:|:------------------|-------------:|
|           443 | Portfolio tonight |       284731 |
|           795 | Portfolio dark    |       282917 |
|           583 | Portfolio movie   |       272766 |

_... 7 more rows_

---

### Q20 - What is the average portfolio value per risk level?

**What went wrong:** Averaged `Projections.FutureValue` instead of the portfolio's current value.

A projection is a forecast for a future date, not what the portfolio is worth now - hence 51,099 against 752.46. Nothing in the raw schema says which of the two a person means by "portfolio value"; the documentation does.

**Correct - Vanna (RAG):**

```sql
SELECT TOP 1000 p.RiskLevel, AVG(ps.TotalPortfolioValue) AS AveragePortfolioValue FROM Portfolios AS p JOIN PortfolioSummary AS ps ON p.PortfolioID = ps.PortfolioID GROUP BY p.RiskLevel
```

| RiskLevel   |   AveragePortfolioValue |
|:------------|------------------------:|
| Low         |                 752.456 |
| High        |                 729.899 |
| Medium      |                 792.747 |

**Wrong - LangChain (zero-shot):**

```sql
SELECT TOP 1000 [RiskLevel], AVG([FutureValue]) AS [AveragePortfolioValue] FROM [Projections] AS p JOIN [Portfolios] AS po ON p.[PortfolioID] = po.[PortfolioID] GROUP BY [RiskLevel]
```

| RiskLevel   |   AveragePortfolioValue |
|:------------|------------------------:|
| Low         |                 51099.1 |
| High        |                 51710.5 |
| Medium      |                 51669.9 |

---

### Q21 - How many clients with a Low risk profile have a High risk portfolio?

**What went wrong:** `COUNT(*)` instead of `COUNT(DISTINCT c.ClientID)`.

A client can hold several high-risk portfolios, so the join produces one row per portfolio and the same client is counted more than once: 111 rows for 90 actual clients.

**Correct - Vanna (RAG):**

```sql
SELECT TOP 1000 COUNT(DISTINCT c.ClientID) AS NumberOfClients FROM Clients AS c JOIN Portfolios AS p ON c.ClientID = p.ClientID WHERE c.RiskProfile = 'Low' AND p.RiskLevel = 'High'
```

|   NumberOfClients |
|------------------:|
|                90 |

**Wrong - LangChain (zero-shot):**

```sql
SELECT TOP 1000 COUNT(*) AS Count FROM [Clients] AS c JOIN [Portfolios] AS p ON c.[ClientID] = p.[ClientID] WHERE c.[RiskProfile] = 'Low' AND p.[RiskLevel] = 'High'
```

|   Count |
|--------:|
|     111 |

---

### Q29 - How many portfolios contain more than 5 assets?

**What went wrong:** Counted rows in `PortfolioAssets` rather than distinct assets.

`HAVING COUNT(AssetID) > 5` counts allocation rows, and the same asset can appear twice in a portfolio. `PortfolioSummary.NumberOfAssets` uses `COUNT(DISTINCT AssetID)` - 86 portfolios, not 88.

**Correct - Vanna (RAG):**

```sql
SELECT TOP 1000 COUNT(*) AS NumberOfPortfolios FROM PortfolioSummary WHERE NumberOfAssets > 5
```

|   NumberOfPortfolios |
|---------------------:|
|                   86 |

**Wrong - LangChain (zero-shot):**

```sql
SELECT TOP 1000 COUNT(*) AS PortfolioCount FROM (SELECT [PortfolioID] AS [PortfolioID] FROM [PortfolioAssets] GROUP BY [PortfolioID] HAVING COUNT([AssetID]) > 5) AS Subquery
```

|   PortfolioCount |
|-----------------:|
|               88 |

---

## Reproduce

```bash
./run.sh          # the app on http://localhost:8085
./run.sh --eval   # re-run all 30 questions through both pipelines
```

The evaluation questions are held out of Vanna's training data: ChromaDB contains only the table/view/
procedure DDL, the `INFORMATION_SCHEMA` plan and `Documentation.txt`.
