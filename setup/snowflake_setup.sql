-- Snowflake setup for the Coco PR Review GitHub Actions reviewer.
--
-- This provisions a SERVICE user that authenticates from GitHub Actions using
-- GitHub OIDC + Snowflake Workload Identity Federation (WIF). No long-lived
-- secrets (passwords, key pairs) are created or stored.
--
-- Replace every <PLACEHOLDER> before running. Run as a role that can create
-- users/roles and grant Cortex usage (e.g. ACCOUNTADMIN or a delegated admin).
--
-- Placeholders:
--   <SERVICE_USER>   Snowflake service user name        e.g. SVC_COCO_REVIEW
--   <REVIEWER_ROLE>  Role granted to the service user   e.g. COCO_REVIEWER
--   <WAREHOUSE>      Warehouse the reviewer may use      e.g. XS_WH
--   <OWNER>/<REPO>   GitHub repository (lowercase)       e.g. my-org/my-repo
--   <NETWORK_POLICY> A network policy whose allowed rules include GitHub
--                    Actions egress IPs (see note at the bottom).

USE ROLE ACCOUNTADMIN;

-- 1. Role + grants ----------------------------------------------------------
CREATE ROLE IF NOT EXISTS <REVIEWER_ROLE>;
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE <REVIEWER_ROLE>;
GRANT USAGE ON WAREHOUSE <WAREHOUSE> TO ROLE <REVIEWER_ROLE>;

-- 2. Service user bound to GitHub OIDC via Workload Identity Federation ------
-- IMPORTANT: the SUBJECT must match the GitHub OIDC `sub` claim of the run.
-- By default the claim differs per trigger:
--   pull_request event:   repo:<OWNER>/<REPO>:pull_request
--   workflow_dispatch:    repo:<OWNER>/<REPO>:ref:refs/heads/<BRANCH>
-- To cover every trigger with ONE subject, pin a stable claim once (run as a
-- repo admin, outside Snowflake):
--   echo '{"use_default":false,"include_claim_keys":["repository"]}' \
--     | gh api --method PUT repos/<OWNER>/<REPO>/actions/oidc/customization/sub --input -
-- The token subject is then always `repo:<OWNER>/<REPO>`, which is what the
-- SUBJECT below uses.
CREATE USER IF NOT EXISTS <SERVICE_USER>
  TYPE = SERVICE
  DEFAULT_ROLE = <REVIEWER_ROLE>
  DEFAULT_WAREHOUSE = <WAREHOUSE>
  COMMENT = 'GitHub Actions OIDC service user for Coco PR review'
  WORKLOAD_IDENTITY = (
    TYPE = OIDC,
    ISSUER = 'https://token.actions.githubusercontent.com',
    SUBJECT = 'repo:<OWNER>/<REPO>'
  );

-- Keep LOGIN_NAME equal to the user name. WIF rejects a login request whose
-- LOGIN_NAME does not match the federated user.
ALTER USER <SERVICE_USER> SET LOGIN_NAME = '<SERVICE_USER>';

GRANT ROLE <REVIEWER_ROLE> TO USER <SERVICE_USER>;

-- 3. Network access ----------------------------------------------------------
-- If your account enforces an account-level network policy, the GitHub-hosted
-- runner IP will be blocked. Attach a user-scoped policy whose allowed rules
-- include GitHub Actions egress ranges. Snowflake ships a managed rule:
--   SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL
-- Either reuse an existing policy that references it, or create one:
--
--   CREATE NETWORK POLICY IF NOT EXISTS <NETWORK_POLICY>
--     ALLOWED_NETWORK_RULE_LIST = ('SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL');
--
ALTER USER <SERVICE_USER> SET NETWORK_POLICY = <NETWORK_POLICY>;

-- 4. Verify ------------------------------------------------------------------
SHOW USERS LIKE '<SERVICE_USER>';
SHOW GRANTS TO ROLE <REVIEWER_ROLE>;
