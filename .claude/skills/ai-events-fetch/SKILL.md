---
name: ai-events-fetch
description: Backfill or top up the AI_EVENTS_RAW bronze table from Exa neural search and rebuild AI_EVENTS via CALL CURATE_AI_EVENTS(). Use when the user wants to refresh the real-news seed, add a new query to the pipeline, or debug missing event coverage.
---

The AI-events ingestion pipeline lives at `backend/src/spcs_si/features/ai_event_impact/`. It walks `NEWS_QUERIES` × monthly chunks, inserts each Exa result into `AI_EVENTS_RAW` (bronze, dedup on `result_url`), then calls the SQL stored procedure `SPCS_SI.APP.CURATE_AI_EVENTS()` which TRUNCATEs and rebuilds `AI_EVENTS` (silver) using `CORTEX.COMPLETE` + `CORTEX.SENTIMENT`.

Driver is the Makefile:

- `make seed-events` — full backfill (idempotent at URL level; safe to re-run).
- `make refresh-events` — top-up from `MAX(result_published)+1d` to today.

The CLI directly:

```
uv --directory backend run python -m spcs_si.features.ai_event_impact.cli backfill [--from YYYY-MM-DD] [--to YYYY-MM-DD]
uv --directory backend run python -m spcs_si.features.ai_event_impact.cli update
uv --directory backend run python -m spcs_si.features.ai_event_impact.cli fetch    [--from YYYY-MM-DD] [--to YYYY-MM-DD]
uv --directory backend run python -m spcs_si.features.ai_event_impact.cli curate
```

Exit codes: `0` clean; `1` hard-fail (missing `EXA_API_KEY`, missing connection, auth error); `2` partial success (one or more chunks were skipped — the run still curated whatever made it through).

Prerequisites: `EXA_API_KEY` in `backend/.env`, `SNOWFLAKE_CONNECTION_NAME` set, `make snowflake-setup` already deployed the bronze table + procedure.

To change what the pipeline searches for, edit `NEWS_QUERIES` in `backend/src/spcs_si/features/ai_event_impact/exa_client.py`. To tune curation (event-type vocabulary, prompt, sentiment), edit `snowflake/sources/definitions/ai_event_impact/curate_ai_events.sql` and redeploy via `make snowflake-setup`.
