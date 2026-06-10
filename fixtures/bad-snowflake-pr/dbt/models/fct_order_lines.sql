-- INTENTIONALLY BUGGY FIXTURE — do not merge.
-- This model is at order-LINE grain (order_id + line_number), but the
-- incremental unique_key is order_id alone, so each incremental merge keeps
-- only one line per order and silently drops the rest. It also filters on
-- updated_at without an is_incremental() guard, so a full refresh and an
-- incremental run produce different results.
{{ config(materialized='incremental', unique_key='order_id') }}

select
    order_id,
    line_number,
    product_id,
    quantity,
    amount,
    updated_at
from {{ source('core', 'order_lines') }}
where updated_at > (select max(updated_at) from {{ this }})
