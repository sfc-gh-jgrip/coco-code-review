-- Customers who have never churned.
-- INTENTIONALLY BUGGY FIXTURE — do not merge.
select customer_id
from analytics.core.customers
where customer_id not in (
    -- churned_customers.customer_id is nullable, so a single NULL here makes
    -- the whole NOT IN predicate return no rows.
    select customer_id from analytics.core.churned_customers
);
