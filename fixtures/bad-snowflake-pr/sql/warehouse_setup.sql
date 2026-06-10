-- Warehouse for a small hourly aggregation job.
-- INTENTIONALLY BUGGY FIXTURE — do not merge.
create or replace warehouse reporting_wh
    warehouse_size = '4X-LARGE'   -- wildly oversized for a small hourly job
    auto_suspend = 3600           -- idles a full hour before suspending (wasted credits)
    auto_resume = true
    min_cluster_count = 4         -- always-on multi-cluster floor for a light load
    max_cluster_count = 10
    initially_suspended = false;  -- bills from the moment it is created
