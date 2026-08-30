# Text-to-SQL evaluation: Vanna (RAG) vs LangChain (zero-shot)

Model: Groq `qwen/qwen3.6-27b` · 30 questions from `eval/questions.json` · metric: execution accuracy (result rows match the reference SQL's rows).

| System | Correct | Accuracy | Avg latency |
|---|---|---|---|
| LangChain (zero-shot) | 25/30 | **83.3%** | 17.6s |
| Vanna (RAG) | 30/30 | **100.0%** | 29.5s |

> Latency is wall-clock per question and **includes waiting on Groq's free-tier rate limit**
> (8,000 tokens/minute), which dominates it - it is not a measure of model speed.

Systems:

- **Vanna (RAG)** - schema DDL + INFORMATION_SCHEMA plan + Documentation.txt embedded in ChromaDB; relevant chunks retrieved per question
- **LangChain (zero-shot)** - `create_sql_query_chain` with the live schema (`SQLDatabase`) in the prompt, no training / documentation

## Per question

| # | tag | question | LangChain (zero-shot) | Vanna (RAG) |
|---|---|---|---|---|
| 1 | view-topn | Which 5 clients have the highest total portfolio value? | ❌ | ✅ |
| 2 | groupby | How many clients are there in each risk profile? | ✅ | ✅ |
| 3 | view | What is the total wealth by asset type? | ✅ | ✅ |
| 4 | join-topn | Which 3 advisors manage the most high-risk clients? | ✅ | ✅ |
| 5 | join-filter | What is the total amount of Buy transactions per account type? | ✅ | ✅ |
| 6 | count | How many advisors are there? | ✅ | ✅ |
| 7 | count | How many portfolios have a high risk level? | ✅ | ✅ |
| 8 | groupby | How many accounts of each account type are there? | ✅ | ✅ |
| 9 | agg | What is the average current value of an asset? | ✅ | ✅ |
| 10 | topn | What are the 5 most valuable assets? | ✅ | ✅ |
| 11 | groupby | How many transactions of each type were made? | ✅ | ✅ |
| 12 | agg-filter | What is the total amount of all Withdraw transactions? | ✅ | ✅ |
| 13 | date | How many transactions happened in 2022? | ✅ | ✅ |
| 14 | date-group | What is the total transaction amount per year? | ✅ | ✅ |
| 15 | join | How many clients does advisor with ID 72 have? | ✅ | ✅ |
| 16 | join-topn | Which 5 advisors have the most clients? | ✅ | ✅ |
| 17 | view-topn | Show the 10 portfolios with the highest total value. | ❌ | ✅ |
| 18 | view | How many assets does portfolio 1 contain? | ✅ | ✅ |
| 19 | join-agg | What is the total portfolio value of client Nathan Perry? | ✅ | ✅ |
| 20 | join-agg | What is the average portfolio value per risk level? | ❌ | ✅ |
| 21 | join-filter | How many clients with a Low risk profile have a High risk portfolio? | ❌ | ✅ |
| 22 | join-agg | Which asset type has the highest total allocation across all portfolios? | ✅ | ✅ |
| 23 | projection | What is the highest projected future value of any portfolio? | ✅ | ✅ |
| 24 | projection | How many projections are dated after 2028? | ✅ | ✅ |
| 25 | join-topn | Which 3 clients have the most accounts? | ✅ | ✅ |
| 26 | join-filter | How many Investment accounts belong to High risk clients? | ✅ | ✅ |
| 27 | agg | What is the largest single transaction amount? | ✅ | ✅ |
| 28 | join-agg | What is the total Sell amount for Stock assets? | ✅ | ✅ |
| 29 | view | How many portfolios contain more than 5 assets? | ❌ | ✅ |
| 30 | multi-join | Which advisor's clients have the highest combined portfolio value? | ✅ | ✅ |

## By question type

| tag | LangChain (zero-shot) | Vanna (RAG) |
|---|---|---|
| agg | 100% | 100% |
| agg-filter | 100% | 100% |
| count | 100% | 100% |
| date | 100% | 100% |
| date-group | 100% | 100% |
| groupby | 100% | 100% |
| join | 100% | 100% |
| join-agg | 75% | 100% |
| join-filter | 67% | 100% |
| join-topn | 100% | 100% |
| multi-join | 100% | 100% |
| projection | 100% | 100% |
| topn | 100% | 100% |
| view | 67% | 100% |
| view-topn | 0% | 100% |

## Failures

**Q1 · LangChain (zero-shot)** - Which 5 clients have the highest total portfolio value?

```sql
SELECT TOP 5 [c].[Name], SUM([a].[CurrentValue] * ([pa].[Allocation] / 100.0)) AS [TotalPortfolioValue] FROM [Clients] AS [c] JOIN [Portfolios] AS [p] ON [c].[ClientID] = [p].[ClientID] JOIN [PortfolioAssets] AS [pa] ON [p].[PortfolioID] = [pa].[PortfolioID] JOIN [Assets] AS [a] ON [pa].[AssetID] = [a].[AssetID] GROUP BY [c].[Name] ORDER BY [TotalPortfolioValue] DESC
```
Error: `nan`

**Q17 · LangChain (zero-shot)** - Show the 10 portfolios with the highest total value.

```sql
SELECT TOP 10 p.[PortfolioID], p.[Name], SUM(pa.[Allocation] * a.[CurrentValue]) AS TotalValue FROM [Portfolios] AS p JOIN [PortfolioAssets] AS pa ON p.[PortfolioID] = pa.[PortfolioID] JOIN [Assets] AS a ON pa.[AssetID] = a.[AssetID] GROUP BY p.[PortfolioID], p.[Name] ORDER BY TotalValue DESC
```
Error: `nan`

**Q20 · LangChain (zero-shot)** - What is the average portfolio value per risk level?

```sql
SELECT TOP 1000 [RiskLevel], AVG([FutureValue]) AS [AveragePortfolioValue] FROM [Projections] AS p JOIN [Portfolios] AS po ON p.[PortfolioID] = po.[PortfolioID] GROUP BY [RiskLevel]
```
Error: `nan`

**Q21 · LangChain (zero-shot)** - How many clients with a Low risk profile have a High risk portfolio?

```sql
SELECT TOP 1000 COUNT(*) AS Count FROM [Clients] AS c JOIN [Portfolios] AS p ON c.[ClientID] = p.[ClientID] WHERE c.[RiskProfile] = 'Low' AND p.[RiskLevel] = 'High'
```
Error: `nan`

**Q29 · LangChain (zero-shot)** - How many portfolios contain more than 5 assets?

```sql
SELECT TOP 1000 COUNT(*) AS PortfolioCount FROM (SELECT [PortfolioID] AS [PortfolioID] FROM [PortfolioAssets] GROUP BY [PortfolioID] HAVING COUNT([AssetID]) > 5) AS Subquery
```
Error: `nan`
