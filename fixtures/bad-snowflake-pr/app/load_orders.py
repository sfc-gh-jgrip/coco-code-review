"""Loader utilities. INTENTIONALLY BUGGY FIXTURE — do not merge."""

import snowflake.connector

# Hardcoded credential committed to source.
SNOWFLAKE_PASSWORD = "S3cr3t-Pa55w0rd!"


def fetch_orders(conn, customer_id):
    cur = conn.cursor()
    # SQL injection: customer_id is interpolated straight into the statement.
    cur.execute(f"select * from orders where customer_id = {customer_id}")
    return cur.fetchall()


def connect():
    return snowflake.connector.connect(
        account="acme",
        user="loader",
        password=SNOWFLAKE_PASSWORD,
    )
