# Ablation: what Vanna training data matters?

Model: Groq `qwen/qwen3.6-27b` · 12 questions from `eval/questions.json` · metric: execution accuracy (result rows match the reference SQL's rows).

| System | Correct | Accuracy | Avg latency |
|---|---|---|---|
| A: DDL only | 10/12 | **83.3%** | 76.9s |
| B: DDL + schema plan | 9/12 | **75.0%** | 21.4s |
| C: DDL + plan + docs | 12/12 | **100.0%** | 30.1s |
| D: C + 5 few-shot pairs | 11/12 | **91.7%** | 31.9s |

> Latency is wall-clock per question and **includes waiting on Groq's free-tier rate limit**
> (8,000 tokens/minute), which dominates it - it is not a measure of model speed.

Systems:

- **A** - table/view/procedure DDL only
- **B** - A + auto-generated INFORMATION_SCHEMA training plan
- **C** - B + hand-written Documentation.txt (= production setup)
- **D** - C + 5 question/SQL examples (held out from the eval set)

## Per question

| # | tag | question | A: DDL only | B: DDL + schema plan | C: DDL + plan + docs | D: C + 5 few-shot pairs |
|---|---|---|---|---|---|---|
| 1 | view-topn | Which 5 clients have the highest total portfolio value? | ✅ | ✅ | ✅ | ✅ |
| 2 | groupby | How many clients are there in each risk profile? | ✅ | ✅ | ✅ | ✅ |
| 3 | view | What is the total wealth by asset type? | ✅ | ✅ | ✅ | ✅ |
| 4 | join-topn | Which 3 advisors manage the most high-risk clients? | ✅ | ❌ | ✅ | ✅ |
| 14 | date-group | What is the total transaction amount per year? | ✅ | ✅ | ✅ | ✅ |
| 16 | join-topn | Which 5 advisors have the most clients? | ✅ | ✅ | ✅ | ✅ |
| 17 | view-topn | Show the 10 portfolios with the highest total value. | ✅ | ✅ | ✅ | ✅ |
| 20 | join-agg | What is the average portfolio value per risk level? | ❌ | ❌ | ✅ | ✅ |
| 21 | join-filter | How many clients with a Low risk profile have a High risk portfolio? | ❌ | ❌ | ✅ | ❌ |
| 28 | join-agg | What is the total Sell amount for Stock assets? | ✅ | ✅ | ✅ | ✅ |
| 29 | view | How many portfolios contain more than 5 assets? | ✅ | ✅ | ✅ | ✅ |
| 30 | multi-join | Which advisor's clients have the highest combined portfolio value? | ✅ | ✅ | ✅ | ✅ |

## By question type

| tag | A: DDL only | B: DDL + schema plan | C: DDL + plan + docs | D: C + 5 few-shot pairs |
|---|---|---|---|---|
| date-group | 100% | 100% | 100% | 100% |
| groupby | 100% | 100% | 100% | 100% |
| join-agg | 50% | 50% | 100% | 100% |
| join-filter | 0% | 0% | 100% | 0% |
| join-topn | 100% | 50% | 100% | 100% |
| multi-join | 100% | 100% | 100% | 100% |
| view | 100% | 100% | 100% | 100% |
| view-topn | 100% | 100% | 100% | 100% |

## Failures

**Q20 · A: DDL only** - What is the average portfolio value per risk level?

```sql
SELECT TOP 1000 p.RiskLevel, AVG(pa.Allocation * a.CurrentValue / 100.0) AS AveragePortfolioValue FROM Portfolios AS p JOIN PortfolioAssets AS pa ON p.PortfolioID = pa.PortfolioID JOIN Assets AS a ON pa.AssetID = a.AssetID GROUP BY p.RiskLevel
```
Returned rows differ from the reference.

**Q21 · A: DDL only** - How many clients with a Low risk profile have a High risk portfolio?

```sql
SELECT TOP 1000 COUNT(*) FROM Clients AS c JOIN Portfolios AS p ON c.ClientID = p.ClientID WHERE c.RiskProfile = 'Low' AND p.RiskLevel = 'High'
```
Returned rows differ from the reference.

**Q4 · B: DDL + schema plan** - Which 3 advisors manage the most high-risk clients?

```sql
(no SQL)
```
Error: `APITimeoutError: Request timed out.`

**Q20 · B: DDL + schema plan** - What is the average portfolio value per risk level?

```sql
SELECT TOP 1000 RiskLevel, AVG(TotalPortfolioValue) AS AveragePortfolioValue FROM PortfolioSummary GROUP BY RiskLevel
```
Error: `DatabaseError: Execution failed on sql 'SELECT TOP 1000 RiskLevel, AVG(TotalPortfolioValue) AS AveragePortfolioValue FROM PortfolioSumm`

**Q21 · B: DDL + schema plan** - How many clients with a Low risk profile have a High risk portfolio?

```sql
SELECT TOP 1000 COUNT(*) AS NumberOfClients FROM Clients AS c JOIN Portfolios AS p ON c.ClientID = p.ClientID WHERE c.RiskProfile = 'Low' AND p.RiskLevel = 'High'
```
Returned rows differ from the reference.

**Q21 · D: C + 5 few-shot pairs** - How many clients with a Low risk profile have a High risk portfolio?

```sql
SELECT TOP 1000 COUNT(*) AS n FROM Clients AS c JOIN Portfolios AS p ON p.ClientID = c.ClientID WHERE c.RiskProfile = 'Low' AND p.RiskLevel = 'High'
```
Returned rows differ from the reference.
