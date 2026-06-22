# Fleet Monitoring Dashboard — runtime image.
# Build context = repo root (`docker compose build` from d:\nkp-ops).
#
# Two services run from this image (see docker-compose.yml):
#   dashboard  — serves http://localhost:8765/ + /refresh + /status
#   cron       — fires the pipeline once per day at $DAILY_RUN_TIME (UTC)
#
# All persistent state (snapshots, timeseries, dashboard.html, logs) lives in
# /app/projects/fleet_monitoring/data, which compose mounts as a bind volume
# so the host repo's audit trail keeps growing on every run.

# syntax=docker/dockerfile:1
FROM python:3.11-slim

# System deps — curl powers the bot-probe subprocess in probes.py.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first so the layer cache survives source edits.
COPY projects/fleet_monitoring/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# App source — explicit COPYs keep the image lean (no docs/, tests/, etc.).
COPY core /app/core
COPY skills /app/skills
COPY projects/__init__.py /app/projects/__init__.py
COPY projects/fleet_monitoring /app/projects/fleet_monitoring
# Overlay tracker + cf-rule-changes log are read by the pipeline.
COPY data/reports/fleet-bandwidth-audit-2026-04-30/monitoring/fixed-sites.yml \
     /app/data/reports/fleet-bandwidth-audit-2026-04-30/monitoring/fixed-sites.yml
COPY data/reports/cf-rule-changes-2026-05-13.jsonl \
     /app/data/reports/cf-rule-changes-2026-05-13.jsonl
COPY data/reports/cf-rule-changes-2026-05-05.jsonl \
     /app/data/reports/cf-rule-changes-2026-05-05.jsonl
COPY data/reports/cf-rule-changes-2026-05-01.jsonl \
     /app/data/reports/cf-rule-changes-2026-05-01.jsonl

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    LISTEN_HOST=0.0.0.0

EXPOSE 8765

# Default = serve the dashboard. The cron service overrides this CMD.
CMD ["python", "-m", "projects.fleet_monitoring.serve"]
