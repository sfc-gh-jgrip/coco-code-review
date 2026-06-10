-- Reporting view: revenue per customer.
-- INTENTIONALLY BUGGY FIXTURE — do not merge.
create or replace view analytics.reporting.revenue_by_customer as
select
    c.customer_id,
    c.customer_name,
    sum(o.amount)        as total_revenue,   -- fanned-out grain: double counts
    count(o.order_id)    as order_count
from analytics.core.customers c
left join analytics.core.order_lines o
    on o.customer_id = c.customer_id
where o.status = 'COMPLETED'                  -- predicate on right side of LEFT JOIN
group by 1, 2;
