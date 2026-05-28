# Coco PR Review

Snowflake Cortex Code-powered GitHub Pull Request review agent.

## Current state

This repository now includes a first-pass GitHub Actions workflow at `.github/workflows/coco-pr-review.yml` and a package entrypoint at `python -m coco_pr_review`.

The workflow is intended to run on:

- `pull_request` for `opened`, `synchronize`, and `reopened`
- `issue_comment` when a pull request comment includes `@coco-review`

The runtime currently relies on the existing event parsing and review pipeline in `src/coco_pr_review/github_event.py`.

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
