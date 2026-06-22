# Fleet Monitoring Dashboard

Fleet-wide monitoring — bandwidth trends, Cloudflare config-drift detection, and
bot-attack alerting across all WP installs and CF zones. Produces a local tabbed
`dashboard.html`.

**Spec:** `docs/superpowers/specs/2026-05-15-fleet-monitoring-dashboard-design.md`
**Plan:** `docs/superpowers/plans/2026-05-15-fleet-monitoring-dashboard.md`

## Running it

From the repo root (`d:\nkp-ops`):

    python projects/fleet_monitoring/run.py                # collect + analyze + render
    python projects/fleet_monitoring/run.py --no-probes    # faster — skip bot probes
    python projects/fleet_monitoring/run.py --render        # just rebuild dashboard.html

Then open `projects/fleet_monitoring/dashboard.html` in a browser.

A full run takes ~10–20 minutes (248 WPE installs + 268 CF zones). Requires
`WPE_API_USER`, `WPE_API_PASSWORD`, and `CF_API_TOKEN` in `.env`.

## Refresh button (local server)

`dashboard.html` has a **⟳ Refresh data** button in the header. It only works
when the dashboard is served via the local HTTP helper — opening the file
directly via `file://` makes the button show an instructions alert.

To use it, run the server once from the repo root and leave it open:

    python -m projects.fleet_monitoring.serve

Then open <http://localhost:8765/> in the browser. Clicking Refresh triggers
the pipeline in a background thread on the server, polls `/status` every 2 s,
and reloads the page when the run finishes (~10–13 min). Multiple clicks
during a run are coalesced (the second `/refresh` is a no-op until the first
completes). Stop the server with Ctrl+C.

## Docker (server-ready)

For server deploys (or just to keep the local PC clean), the dashboard ships
with a small Docker stack:

| Service | Image | Role |
|---|---|---|
| `dashboard` | `nkp-fleet-monitoring:latest` | Serves http://localhost:8765/ + handles the Refresh button |
| `cron` | same image | Fires `run.py --no-probes` once per day at `$DAILY_RUN_TIME` UTC |

Both containers mount the host's `projects/fleet_monitoring/data` directory,
so snapshots + timeseries + `dashboard.html` persist on the host and survive
restarts. Secrets are passed via `.env` (mounted, never baked into the image).

Build and start (from the repo root `d:\nkp-ops`):

    docker compose -f projects/fleet_monitoring/docker-compose.yml up -d --build

Tail logs:

    docker compose -f projects/fleet_monitoring/docker-compose.yml logs -f

Stop:

    docker compose -f projects/fleet_monitoring/docker-compose.yml down

To change the daily fire time, edit `DAILY_RUN_TIME` in the compose file
(default `22:00` UTC = 06:00 Manila / UTC+8) and restart the `cron` service.

To deploy on a remote server: clone the repo there, drop a `.env` next to it
with `WPE_API_USER`, `WPE_API_PASSWORD`, and `CF_API_TOKEN`, run the same
`docker compose up -d --build` command, and put your DNS/reverse-proxy of
choice in front of port 8765.

## Pipeline

| Stage | Module | Output |
|-------|--------|--------|
| collect | `collect.py` | `data/snapshots/YYYY-MM-DD.json` + `data/roster.json` |
| analyze | `analyze.py` | alerts written into the snapshot + `data/alerts-latest.json` |
| render  | `render.py`  | `dashboard.html` |
| orchestrate | `run.py` | chains all three, appends `data/timeseries.jsonl`, writes `data/run-log.jsonl` |

## Files you hand-edit

- `config/alerts-mute.yml` — mute known/accepted alerts (permanent or with an
  expiry date)
- `config/wpe-plans.yml` — per-WPE-account plan caps. Pull each account's
  `cycle_start_day`, `bandwidth_gb_limit`, `visits_limit`, and optional
  `overage_per_gb_usd` from `https://my.wpengine.com/account/<id>/usage`.
  Any field left as `null` causes the Plan Utilization panel to SKIP that
  computation for the account — it will still surface current consumption
  from the WPE API. No fake numbers are ever displayed.

### Plan Utilization panel

A new Overview-tab panel surfaces *"am I about to bust a WPE plan?"*. For
each configured account it shows:

- cycle-to-date GB used / plan limit (cycle window labeled)
- linear projection to cycle end
- days elapsed / cycle length
- optional $ overage label when both `bandwidth_gb_limit` AND
  `overage_per_gb_usd` are set AND projection > 100%

Alerts fire at three tiers per axis (bandwidth, visits): **warning** at
80% cycle-to-date, **critical** at 95%, and a separate **warning** when
the linear projection exceeds 100% before cycle end. Unconfigured accounts
emit zero plan-utilization alerts (no honest signal to compute).

Spec: `docs/superpowers/specs/2026-05-19-plan-utilization-design.md`.

### Per-site drill-down

Click a site name in the Sites tab -> dedicated per-site page at
`data/sites/<key>.html`. Surfaces:

- CF configuration summary (SSL, AI bot toggles, bot fight, security level,
  WAF / cache rule counts, proxy state of apex + www)
- Site-specific bandwidth trend (line chart from `timeseries.jsonl`)
- Bot vs human requests over 30 days (per CF bot management classification)
- Top 20 countries by request count
- Top 10 paths and top 10 user agents

Each page has an **Refresh this site** button -- when the dashboard is
served via the local HTTP helper, it hits `POST /refresh-site` which
re-runs the three per-site CF GraphQL queries and rewrites just that one
page. Independent of the fleet-wide Refresh.

Per-site CF analytics also run inside the daily Docker cron (~30-60 s
added for 268 zones x 3 GraphQL queries at concurrency 10), so the pages
have fresh data each morning even without a manual refresh.

Spec: `docs/superpowers/specs/2026-05-19-per-site-drilldown-design.md`.

### Intervention tracking

The dashboard records site fixes and measures whether they worked.

- **Auto-draft:** when the analyze stage detects a CF config change
  attributed to us, it appends a draft to `config/interventions.yml`
  with `status: needs_review` and a guessed type + target metric.
- **Review:** edit `config/interventions.yml` — set `status: confirmed`
  (correct `target_metric` / `applied_date` / `type` if the guess is
  wrong) or `status: dismissed`. You can also hand-add a confirmed entry
  for a fix that drift did not catch. `target_metric` is one of
  `bandwidth`, `mb_per_visit`, `storage`.
- **Measure:** each run rebuilds `data/fleet.db` (SQLite) — a `metrics`
  mirror from `daily.jsonl`, the confirmed `interventions`, and a computed
  `effectiveness` table comparing each fix's target metric averaged over
  the 14 days before vs 7 / 30 / 90 days after.
- **Surface:** the **Interventions** tab shows each fix's verdict
  (`worked` / `no effect` / `regressed` / `too early`) and an aggregate
  "what works by fix type" panel.

`data/fleet.db` is derived (gitignored, rebuilt each run) — open it with
any SQLite tool for ad-hoc queries over the full metric history.

Spec: `docs/superpowers/specs/2026-05-20-intervention-tracking-design.md`.

### Fleet Console

`console.html` is a second, parallel view — a Conceptzilla-style three-column
server console — generated alongside `dashboard.html` each render.

- **Dark sidebar** — cross-navigation: a "Console" item plus links to the
  main dashboard's tabs (`dashboard.html#sites` etc.).
- **Site list** — all sites grouped by WPE account, collapsible, with a live
  search box.
- **Detail panel** — click a site: info card, bandwidth + visits 30d
  sparklines, a bandwidth trend chart, a cache-hit-rate donut, and the site's
  active alerts. A "full page" link jumps to the per-site drill-down page.

The whole console is one self-contained HTML file — all per-site detail is
embedded as JSON and the panel swaps client-side. Open it directly, or via
the local server at <http://localhost:8765/console.html>.

It deliberately does NOT show CPU / RAM / OS / Docker — WP Engine does not
expose those, and the dashboard never displays numbers it cannot source.

Spec: `docs/superpowers/specs/2026-05-20-fleet-console-design.md`.

## Data files

- `data/snapshots/*.json` — raw dated snapshots (committed — audit trail)
- `data/timeseries.jsonl` — compact per-site daily rollup, chart source (committed)
- `data/roster.json`, `data/alerts-latest.json`, `data/run-log.jsonl` — run state (gitignored)
- `dashboard.html` — generated (gitignored)

## Daily scheduled run (Windows Task Scheduler)

The launcher `run-daily.cmd` handles the `cd /d` + python invocation + log
redirect, so the schtasks command is simple:

    schtasks /create /tn "FleetMonitoring" /tr "D:\nkp-ops\projects\fleet_monitoring\run-daily.cmd" /sc daily /st 06:00 /f

The task is already registered on this machine as of 2026-05-18 — verify with:

    schtasks /query /tn "FleetMonitoring" /v /fo LIST

To remove or change the schedule, use `schtasks /delete /tn FleetMonitoring /f`
or edit the task in the Task Scheduler UI.

**Logs**: each daily run appends stdout/stderr to `data/run-daily.log`
(gitignored) and a structured JSON line to `data/run-log.jsonl` — check either
to confirm the task fired. If a whole API source is unreachable the run aborts
cleanly and the previous `dashboard.html` stays in place with a staleness banner.
