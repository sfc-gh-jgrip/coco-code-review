-- Provisioning for the analytics rollout.
-- INTENTIONALLY BUGGY FIXTURE — do not merge.

-- Exposes PII to every role in the account.
grant select on all tables in schema analytics.pii to role public;

-- Far broader than any consumer needs.
grant all privileges on database analytics to role analyst;

-- Hands schema ownership to a broad human role.
grant ownership on schema analytics.core to role data_engineer;

-- External stage with credentials hardcoded in the DDL.
create or replace stage analytics.ext.s3_load
    url = 's3://acme-data/loads/'
    credentials = (
        aws_key_id = 'AKIAIOSFODNN7EXAMPLE'
        aws_secret_key = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
    );
