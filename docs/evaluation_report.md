# Evaluation report

Generated 2026-11-20 with model `gpt-5-nano-2025-08-07`. 61 questions, 3 at a time.

Each question has a reference SQL that is run on the same data; an answer is correct when it returns what the reference returns, not merely when it runs. The data is a private copy, and the run checks that it is unchanged afterwards.

Settings: `ambiguity_check=True`, `ambiguity_parallel=True`, `sql_max_attempts=3`, `query_timeout_seconds=15`.

## Summary

**59 of 61 questions passed (96.7%).** 0 ended in an error before an answer was produced.

## Accuracy

| Metric | Result | What it measures |
|---|---|---|
| Schema linking accuracy | **100%** | Questions where the tables handed to the model include every table the right answer needs (checked before any model call). |
| Table identification accuracy | **95.7%** | Of the tables the right answer reads, the share the generated SQL also reads. |
| Column identification accuracy | **95.7%** | Of the columns the right answer uses, the share the generated SQL also uses. |
| Join correctness | **84.6%** | Questions needing two or more tables where the SQL reads exactly the right set of tables. |
| SQL execution accuracy | **95.7%** | Questions that ended in SQL that passed every check and ran (after any corrections). |
| SQL validation success rate | **93.5%** | Questions whose first SQL passed validation and ran, with no correction needed. |
| Result correctness | **95.7%** | Answers whose rows match the reference result. Extra or reordered columns are allowed; wrong or missing rows are not. |
| Exact result match | **76.1%** | As above, but also requiring exactly the reference's columns. |
| Visualization suitability | **100%** | Answers whose automatic chart is one of the types that suit the question. |

## Ambiguity detection

| Measure | Result |
|---|---|
| Vague questions that were asked about (recall) | **100%** |
| Clear questions that were asked about needlessly (false alarms) | **4.3%** |
| Questions asked about that really were vague (precision) | **66.7%** |
| F1 | **80%** |

## Safety

| Measure | Result |
|---|---|
| Unsafe query rejection rate (refused outright, not answered) | **33.3%** over 6 requests |
| Data unchanged after the request | **100%** |
| Blocked values kept out of answers | **100%** |
| Handled safely overall (nothing changed, nothing leaked, nothing but a read-only query run) | **100%** |
| Questions about data that does not exist handled without inventing it | **100%** over 5 questions |

## Query execution time

| Measure | Result |
|---|---|
| Whole question, mean | **6.0 s** |
| Whole question, median | 5.3 s |
| Whole question, 95th percentile | 10.6 s |
| Running the SQL on the database, mean (48 queries) | 0.4 ms |
| Running the SQL on the database, 95th percentile | 1.0 ms |

The whole-question time includes the model calls, which dominate it.

## By category

| Category | Passed | Questions | Rate |
|---|---|---|---|
| simple selection | 4 | 4 | 100% |
| filtering | 4 | 5 | 80% |
| aggregation | 5 | 5 | 100% |
| sorting | 3 | 3 | 100% |
| single join | 3 | 4 | 75% |
| multiple joins | 4 | 4 | 100% |
| time analysis | 3 | 3 | 100% |
| comparison | 5 | 5 | 100% |
| business definitions | 3 | 3 | 100% |
| ambiguity | 4 | 4 | 100% |
| invalid schema | 5 | 5 | 100% |
| unsafe SQL | 6 | 6 | 100% |
| follow-up questions | 7 | 7 | 100% |
| uploaded data | 3 | 3 | 100% |

## Failures

### `filter_big_card` (filtering)

- **Question:** How many card payments were over 200?
- **Expected:** a result matching the reference answer
- **Got:** clarification
- **Asked:** Please provide data to compute the count of card payments over 200.

Reference:

```sql
SELECT COUNT(*) FROM payments WHERE payment_method = 'card' AND amount > 200
```

### `join_ava_products` (single join)

- **Question:** Which products has the customer Ava Miller ordered?
- **Expected:** a result matching the reference answer
- **Got:** clarification
- **Asked:** Which specific product identifiers would you like for Ava Miller's orders?

Reference:

```sql
SELECT DISTINCT p.product_name FROM customers c JOIN orders o ON o.customer_id = c.customer_id JOIN products p ON p.product_id = o.product_id WHERE c.customer_name = 'Ava Miller'
```
