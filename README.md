# Coco PR Review

Snowflake Cortex Code-powered GitHub Pull Request review agent.

## Current state

This repository now includes a first-pass GitHub Actions workflow at `.github/workflows/coco-pr-review.yml` and a package entrypoint at `python -m coco_pr_review`.

The workflow is intended to run on:

- `pull_request` for `opened`, `synchronize`, and `reopened`
- `issue_comment` when a pull request comment includes `@coco-review`

The runtime currently relies on the existing event parsing and review pipeline in `src/coco_pr_review/github_event.py`.

## GitHub Actions authentication

The workflow does not need a separate GitHub login for reading the PR or posting review output. It uses the repository-scoped `${{ secrets.GITHUB_TOKEN }}` that GitHub Actions injects automatically for each run.

Required setup:

- Enable GitHub Actions for the repository.
- Keep the workflow permissions aligned with `.github/workflows/coco-pr-review.yml`: `contents: read`, `pull-requests: write`, `issues: write`, and `checks: write`.
- Do not add a personal access token for normal operation unless you intentionally need broader cross-repository access than the built-in `GITHUB_TOKEN` provides.

How it works:

- `src/coco_pr_review/github_event.py` reads `GITHUB_TOKEN` from the workflow environment and uses it to construct the GitHub API client.
- The workflow passes `${{ secrets.GITHUB_TOKEN }}` into the `Run Coco PR review` step, so the reviewer can fetch PR metadata and publish comments/check output during that job.

## Snowflake authentication

Snowflake authentication is separate from GitHub authentication.

- GitHub authentication uses `${{ secrets.GITHUB_TOKEN }}` so the workflow can read pull request metadata and publish comments or checks.
- Snowflake authentication is required so the review runtime can execute Cortex Code through the Python SDK.
- This repository calls `cortex-code-agent-sdk` directly from `src/coco_pr_review/github_event.py`, and that SDK is backed by Cortex Code at runtime.

Recommended model:

- Create a dedicated Snowflake service identity for this automation instead of using a human user.
- Use GitHub Actions OIDC with Snowflake Workload Identity Federation (WIF) instead of long-lived passwords or key files.
- Keep Snowflake authentication configuration in GitHub Actions environment/variables only.
- Do not commit `connections.toml`, private keys, passwords, or generated auth files to this repository.

This repository's concrete deployment contract is GitHub OIDC via Workload Identity Federation.

### Required configuration (repository variables)

All Snowflake configuration is supplied through GitHub Actions **repository variables**
(Settings → Secrets and variables → Actions → Variables). These are identifiers, not
credentials, so they are variables rather than secrets — Workload Identity Federation
means there is **no long-lived secret to store** (no password, key pair, or token).

| Variable | Example | Purpose |
|----------|---------|---------|
| `SNOWFLAKE_ACCOUNT` | `myorg-myaccount` | Account identifier |
| `SNOWFLAKE_HOST` | `abc12345.eu-central-1.snowflakecomputing.com` | Account host |
| `SNOWFLAKE_USER` | `SVC_COCO_REVIEW` | Service user bound to GitHub OIDC |
| `SNOWFLAKE_ROLE` | `COCO_REVIEWER` | Role for the service user |
| `SNOWFLAKE_WAREHOUSE` | `XS_WH` | Warehouse the reviewer may use |

The only secret used is the built-in `${{ secrets.GITHUB_TOKEN }}`, which GitHub injects
automatically. If you prefer to source the values above from an external key vault, fetch
them in a prior step and set them as outputs/env — the workflows only read them via
`${{ vars.* }}`.

Also required:

- Workflow permission `id-token: write` (already set in both workflows) so GitHub can mint
  the OIDC token used for Snowflake authentication.

### Snowflake-side setup

The Snowflake objects are not created by the workflow; provision them once per account.
Run [`setup/snowflake_setup.sql`](setup/snowflake_setup.sql) (fill in the placeholders).
It creates:

- A `TYPE = SERVICE` user bound to GitHub OIDC via `WORKLOAD_IDENTITY = (TYPE = OIDC, ISSUER = 'https://token.actions.githubusercontent.com', SUBJECT = '<repo subject>')`.
- A role with `SNOWFLAKE.CORTEX_USER` and `USAGE` on the warehouse.
- `LOGIN_NAME` equal to the user name (WIF rejects a mismatched `LOGIN_NAME`).
- A user-scoped network policy that allows GitHub Actions egress IPs (e.g. via the managed rule `SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL`), if your account enforces an account-level network policy.

The OIDC `sub` claim must match the configured `SUBJECT`. By default the claim
differs by trigger (`pull_request` → `repo:<owner>/<repo>:pull_request`,
`workflow_dispatch` → `repo:<owner>/<repo>:ref:refs/heads/<branch>`), which would
require a separate subject per trigger. To support every trigger with a single
subject, this repo pins a **stable subject claim** via GitHub's OIDC customization:

```bash
echo '{"use_default":false,"include_claim_keys":["repository"]}' \
  | gh api --method PUT repos/<OWNER>/<REPO>/actions/oidc/customization/sub --input -
```

That makes the token subject always `repo:<owner>/<repo>` regardless of trigger, so
the WIF `SUBJECT` is simply `repo:<owner>/<repo>`. Tradeoff: any workflow in the repo
can assume the identity, which is acceptable for this low-privilege reviewer user
(Cortex usage + one warehouse). Tighten the claim keys if you need a narrower scope.

### How authentication works at runtime

The reviewer authenticates to Snowflake with a short-lived GitHub OIDC token
(audience `snowflakecomputing.com`) written to a Cortex-readable
`~/.snowflake/connections.toml` using `authenticator = WORKLOAD_IDENTITY` — no
secret is persisted. The top-level composite action ([`action.yml`](action.yml))
inlines this OIDC mint; the standalone [`.github/actions/snowflake-oidc`](.github/actions/snowflake-oidc/action.yml)
action exposes the same logic for workflows that wire up the steps themselves.

### Reusing this reviewer in another repository

Adoption is a single step. The composite action installs Cortex Code, mints the
GitHub OIDC token, writes the Snowflake `connections.toml`, and runs the reviewer.

1. Provision the Snowflake side however you normally manage your account. The reviewer
   authenticates from GitHub Actions via GitHub OIDC + Workload Identity Federation (no
   long-lived secrets), so it needs:
   - A **service user** (`TYPE = SERVICE`) with `WORKLOAD_IDENTITY` of `TYPE = OIDC`,
     `ISSUER = https://token.actions.githubusercontent.com`, and `SUBJECT` matching your
     repo's OIDC `sub` claim (e.g. `repo:<owner>/<repo>` — pin it stable across triggers
     by customizing the repo's OIDC subject claim).
   - A **role** for that user granted `SNOWFLAKE.CORTEX_USER` and `USAGE` on a **warehouse**.
   - If your account enforces a network policy, a **user-scoped policy** that allows GitHub
     Actions egress (the managed rule `SNOWFLAKE.NETWORK_SECURITY.GITHUBACTIONS_GLOBAL` covers this).

   A copy-paste example with placeholders lives in
   [`setup/snowflake_setup.sql`](setup/snowflake_setup.sql).
2. Set the repository variables above and grant `id-token: write` permission.
3. Add a workflow that checks out the repo and calls the action:

   ```yaml
   permissions:
     id-token: write
     contents: read
     pull-requests: write
     issues: write
     checks: write
   steps:
     - uses: actions/checkout@v4
       with:
         fetch-depth: 0
     - uses: sfc-gh-jgrip/coco-code-review@main
       with:
         snowflake-account: ${{ vars.SNOWFLAKE_ACCOUNT }}
         snowflake-host: ${{ vars.SNOWFLAKE_HOST }}
         snowflake-user: ${{ vars.SNOWFLAKE_USER }}
         snowflake-role: ${{ vars.SNOWFLAKE_ROLE }}
         snowflake-warehouse: ${{ vars.SNOWFLAKE_WAREHOUSE }}
   ```

   `fetch-depth: 0` is required so the diff has full history. For `issue_comment`
   (`@coco-review`) triggers, resolve the PR head SHA and pass it to `checkout`'s
   `ref:` — see [`.github/workflows/coco-pr-review.yml`](.github/workflows/coco-pr-review.yml)
   for the full reference workflow this repo uses to review its own PRs.

#### Action inputs

| Input | Required | Default | Notes |
|-------|----------|---------|-------|
| `snowflake-account` | yes | — | Account identifier (e.g. `myorg-myaccount`). |
| `snowflake-host` | yes | — | Account host (e.g. `abc12345.eu-central-1.snowflakecomputing.com`). |
| `snowflake-user` | yes | — | Service user with `WORKLOAD_IDENTITY`. |
| `snowflake-role` | yes | — | Role for the service user. |
| `snowflake-warehouse` | yes | — | Warehouse for the service user. |
| `github-token` | no | `${{ github.token }}` | API token; ignored when an App token is minted. |
| `coco-app-id` | no | `''` | GitHub App ID; posts comments under the app identity. |
| `coco-app-private-key` | no | `''` | App private key (PEM), paired with `coco-app-id`. |
| `python-version` | no | `3.11` | Python used to install/run the reviewer. |
| `oidc-audience` | no | `snowflakecomputing.com` | OIDC token audience. |
| `log-level` | no | — | Reviewer log level (`DEBUG`, `INFO`, ...). |
| `base-ref` | no | — | Diff base override for branch (push) reviews. |

> **Versioning:** pin to a tagged release once releases are published. Tagged
> releases and version pinning are not yet set up — for now reference `@main`.

What not to do:

- Do not commit auth files or generated connection artifacts to the repo.
- Do not store long-lived Snowflake credentials anywhere — WIF does not need them.
- Do not run this automation under a personal Snowflake user account.

## Local development

Install the package and test dependencies:

```bash
python -m pip install --upgrade pip
pip install -e .[dev]
```

Run tests:

```bash
python -m pytest /Users/jgrip/dev/coco-apps/coco-code-review -q
```

## Testing

The repository uses a simple test pyramid:

- `tests/unit/` covers isolated logic and narrow component behavior.
- `tests/integration/` covers in-memory GitHub workflow boundaries and cross-module behavior without live external services.
- Live GitHub smoke validation is optional and should remain outside the default `pytest` suite and default GitHub Actions runs.

The integration workflow layer is intended to exercise the real review boundaries that matter most in this project, including event parsing, skip logic, sticky comment ownership, rerun deduplication, and publish behavior for both `pull_request` and `issue_comment` triggers.

If you need a real GitHub smoke check, keep it opt-in only:

- Run it manually or from `workflow_dispatch`, not from the default PR workflow.
- Use `.github/workflows/coco-pr-review-smoke.yml` as the manual path for disposable fixture repositories only.
- Use a disposable fixture repository and explicit environment or credential setup.
- Verify only the highest-value path: trigger reception, PR fetch, sticky comment create or update, inline review publish, and check-run output.
- Do not document or imply that live GitHub verification runs by default in this repository today.
