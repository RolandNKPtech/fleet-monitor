# Fleet Monitor

Operator dashboard tracking 248+ WordPress sites across Cloudflare, WP
Engine, GA4, and Google Search Console. Runs as a daily GitHub Actions
pipeline; state and serving live in Cloudflare R2 + Workers.

## Architecture

```
GitHub Actions (cron + manual)
        │
        │ pull state from R2 → run pipeline → push state back
        ▼
Cloudflare R2 (fleet-monitor bucket)
        ▲
        │ read HTML + run-log
        │
Cloudflare Worker (fleet-monitor-api)
        ▲
        │ HTTPS (gated by CF Access)
        │
Operator browser
```

## What the pipeline does

Every run produces a snapshot of fleet health:

1. **collect** — fetches CF zone config + analytics, WPE usage, per-zone
   CF traffic patterns, GA4 sessions/conversions, GSC clicks/impressions
2. **analyze** — runs 17 rules over the snapshot (alerts on bandwidth
   spikes, cert expiry, edge 5xx, cache hit rate, plan changes, token
   failures, etc.)
3. **render** — generates `dashboard.html`, `console.html`, per-site pages

Rules in [`projects/fleet_monitoring/rules/`](projects/fleet_monitoring/rules/).
Outputs in R2 at `fleet/dashboard.html`, `fleet/sites/*.html`,
`fleet/snapshots/YYYY-MM-DD.json`.

## Schedule

- **Daily cron**: 19:00 UTC = 03:00 Manila (delivers fresh data before the
  operator's morning triage window)
- **Manual**: GitHub Actions `workflow_dispatch`, or click Refresh in the
  dashboard (Worker fires the dispatch)
- **Runtime**: ~30-90 min end-to-end (analytics pull + per-zone CF reads)

## Local dev

```bash
pip install -r requirements.txt
python -m projects.fleet_monitoring.run --no-probes
python -m pytest tests/fleet_monitoring/
```

Without R2 env vars set, the pipeline writes everything to
`projects/fleet_monitoring/data/` locally and skips the R2 sync —
exactly the dev-laptop workflow.

## Deploy

Worker + R2 + Access setup lives in [`worker/deploy.md`](worker/deploy.md).

## Secrets required (GitHub Actions)

| Secret | Used by |
|---|---|
| `R2_ACCESS_KEY_ID` | r2_state.py |
| `R2_SECRET_ACCESS_KEY` | r2_state.py |
| `R2_ACCOUNT_ID` | r2_state.py |
| `R2_BUCKET` | r2_state.py |
| `CF_API_TOKEN` | skills/cloudflare (zone reads) |
| `WPE_API_USER` + `WPE_API_PASSWORD` | wpe_api.py |
| `GA4_GSC_CLIENT_ID` | skills/analytics/oauth.py (refresh) |
| `GA4_GSC_CLIENT_SECRET` | skills/analytics/oauth.py (refresh) |
