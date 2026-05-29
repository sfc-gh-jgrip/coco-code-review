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

Snowflake objects provisioned for this repository:

- Service user: `SVC_COCO_REVIEW` (`TYPE = SERVICE`)
- Role: `COCO_REVIEWER`
- Warehouse grant: `USAGE` on `XS_WH`
- Cortex grant: `SNOWFLAKE.CORTEX_USER`
- Workload identity: OIDC issuer `https://token.actions.githubusercontent.com`, subject bound to the GitHub repository

GitHub Actions configuration required for this repository:

1. Set repository variable `SNOWFLAKE_ACCOUNT` to the Snowflake account identifier.
2. Set repository variable `SNOWFLAKE_HOST` to the Snowflake account host, for example `kw35710.eu-central-1.snowflakecomputing.com`.
3. Do not set a Snowflake password, private key, or `connections.toml` secret for this workflow.
4. Keep workflow permission `id-token: write` enabled so GitHub can mint the OIDC token used by Snowflake authentication.

How the Snowflake identity is bound:

- The service user `SVC_COCO_REVIEW` has `WORKLOAD_IDENTITY = (TYPE = OIDC, ISSUER = 'https://token.actions.githubusercontent.com', SUBJECT = '<repo subject>')`.
- The GitHub OIDC `sub` claim must match the configured `SUBJECT`. The claim differs by trigger: a `pull_request` run is `repo:<owner>/<repo>:pull_request`, while a `workflow_dispatch` run is `repo:<owner>/<repo>:ref:refs/heads/<branch>`.
- The workflow authenticates with `snowflakedb/snowflake-cli-action@v2` (`use-oidc: true`), which mints the GitHub OIDC token with audience `snowflakecomputing.com` and exports `SNOWFLAKE_AUTHENTICATOR=WORKLOAD_IDENTITY` plus the token for downstream steps.

For downstream users of this repository:

- The code is intentionally implemented for a single auth contract at a time.
- If another repository wants to reuse this project, it should provision its own service user, role, and OIDC integration matching its GitHub repository identity, then update the workflow environment values accordingly.
- Secret-based service-user login is not implemented as a runtime fallback in this repository.

What not to do:

- Do not commit auth files or generated connection artifacts to the repo.
- Do not store long-lived credentials in plaintext repository variables.
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
