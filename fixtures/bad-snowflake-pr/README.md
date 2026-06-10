# Bad Snowflake PR fixture (do NOT merge)

This directory exists only to exercise the `snowflake` reviewer profile against
deliberately-broken Snowflake/dbt/code changes. Every file here contains planted
defects. It is opened as a throwaway PR so we can confirm the reviewer catches
them; it must never be merged.

## Planted defects by reviewer

| File | Reviewer (expected) | Defects |
|------|---------------------|---------|
| `sql/revenue_by_customer.sql` | `sql-correctness` | join fanout on line-item grain; predicate on the right side of a LEFT JOIN |
| `sql/active_customers.sql` | `sql-correctness` | `NOT IN (subquery)` with nullable column |
| `sql/grants.sql` | `snowflake-governance-security` | `GRANT ... TO PUBLIC`; `GRANT ALL PRIVILEGES`; `GRANT OWNERSHIP` to broad role; hardcoded AWS secret in a stage |
| `sql/warehouse_setup.sql` | `performance-and-cost` | oversized warehouse; long `auto_suspend`; always-on multi-cluster floor; `initially_suspended = false` |
| `dbt/models/fct_order_lines.sql` | `dbt-transformation` | incremental `unique_key` does not match grain; missing `is_incremental()` guard |
| `dbt/models/schema.yml` | `dbt-transformation` | new fact model with no `unique`/`not_null` tests on its key |
| `app/load_orders.py` | `bugs-and-security` | SQL injection via f-string; hardcoded credential |

## What a passing run looks like

- `sql-correctness` and `dbt-transformation` **activate** (SQL + dbt files changed).
- Findings appear for each file above, with the categories noted.
- Cortex logs show each Snowflake reviewer loading its bundled skill via the
  `skill` tool, and no `structured_output missing` fallback warnings.
