# analytics — local data lake for GA4 + GSC

Pulls raw GA4 and Search Console data from our OAuth-token Google users and
stores it as date-partitioned Parquet, queryable through DuckDB. Discovery
runs first each time so newly-shared properties and sites are picked up
automatically.

## Layout

```
data/analytics/
├── meta/
│   ├── properties.parquet      # GA4 properties per token
│   ├── sites.parquet           # GSC sites per token
│   └── token_health.parquet    # per-token discovery status
├── gsc/
│   └── search_analytics/<safe_host>/<YYYY-MM>.parquet
├── ga4/                        # populated in Phase 2
└── state/
    ├── pull_log.jsonl          # one line per run
    └── gsc_errors_<ts>.jsonl   # only when errors occurred
```

Each row in `gsc/search_analytics` is the cross-product of
`date × query × page × country × device` for that site. The host lives
inline in every row (column name `host`) AND in the directory name (without
Hive `key=` syntax so pyarrow's auto-partitioning won't inject a phantom
column on re-read).

## OAuth tokens

Uses the same `alltoken.json` as `scripts/audit_ga4_gsc_access.py`. Currently
3 working tokens (`analyticsuser`, `analyticsuser2`, `analyticsuser3`). Any
revoked tokens are detected at session-load time and skipped — they do not
block runs.

## Commands

```bash
# Discover what's reachable; refresh meta tables.
python -m skills.analytics.discover

# Pull last 7 days of GSC for every site (default incremental).
python -m skills.analytics.gsc_pull

# Full 16-month backfill across all sites.
python -m skills.analytics.gsc_pull --mode full

# One site, custom window — useful for debugging.
python -m skills.analytics.gsc_pull --host example.com --days 30

# DuckDB query against the lake.
python -m skills.analytics.query --report top_queries_28d
python -m skills.analytics.query --sql "SELECT host, sum(clicks) FROM gsc_search_analytics WHERE date >= current_date - 7 GROUP BY host ORDER BY 2 DESC LIMIT 10"

# Or open DuckDB directly:
duckdb data/analytics/views.duckdb
```

## Modes

- **incremental** (default): pulls a rolling N-day window (default 7). GSC
  data finalizes a few days after collection, so the rolling window catches
  late-arriving rows. Idempotent — re-running overwrites the affected dates.
- **full**: pulls the last 480 days (~16 months, the GSC API maximum). Use
  on first install, when a token gains new properties, or to repair after a
  long outage.

## Discovery behavior — auto-pickup of new sites

`discover.py` runs every time the orchestrator (`pull.py`, future) executes.
For each live token it calls `accountSummaries.list` (GA4) and `sites.list`
(GSC) and writes fresh `meta/properties.parquet` + `meta/sites.parquet`.

If a client adds `analyticsuser@gmail.com` as a viewer on a new property
today, tomorrow's run picks it up and `gsc_pull.py`'s `full` mode backfills
its history. No code change.

For sites accessible to multiple tokens, the first-listed account in
`meta/sites.parquet` is used for the pull (recorded in the row's `account`
column). This avoids duplicate pulls and quota waste.

## Storage estimate

- Initial 16-month backfill: ~20-30 GB Parquet (zstd compressed)
- Steady-state growth: ~12-18 GB/year
- Crosses 100 GB after ~5 years of accumulation

## Live lake state (90-day bootstrap, 2026-05-27)

| Source | Rows | Hosts/Properties | Disk |
|---|---:|---:|---:|
| GSC search_analytics | 68.8M | 565 hosts | 367 MB |
| GA4 property_metrics | 32.6k | 435 properties | -- |
| GA4 traffic_sources | 213.8k | 435 properties | -- |
| GA4 top_pages | 2.7M | 435 properties | 60 MB combined |
| **Total** | **~71.7M** | -- | **~430 MB** |

Of 632 GA4 properties discovered, 435 had data in the 90-day window. The
others are staging/parked properties with no active tagging.

29 GSC hosts (4.6%) returned permission errors despite being in `sites.list`
— stored in `data/analytics/state/gsc_errors_*.jsonl` for triage.

## Known limitations

- No orchestrator scheduling yet — invoke modules manually (or via
  `python -m skills.analytics.pull --backfill` for combined GSC+GA4)
- No Windows Task Scheduler setup yet (Phase 3)
- GSC dimensions fixed to (date, query, page, country, device). Other
  dimensions (e.g. searchAppearance) need a separate report.
- GA4 has 3 fixed reports (property_metrics, traffic_sources, top_pages).
  Custom dimensions per property are not pulled.

## Dependencies

```
pip install duckdb pyarrow
```

`google-auth-oauthlib` is only needed for re-minting tokens
(`scripts/mint_oauth_token.py`), not for pulls.
