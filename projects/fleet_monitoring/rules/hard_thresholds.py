"""Rules: hard thresholds that fire regardless of baseline — site_down, insecure_tls."""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL

RULE_ID = "hard_thresholds"   # registry label; emits sub-typed rule ids
INSECURE_TLS = {"1.0", "1.1"}


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    alerts = []
    cfg = (site.get("cf") or {}).get("config") or {}
    settings = cfg.get("settings") or {}
    tls = settings.get("min_tls_version")
    if tls in INSECURE_TLS:
        alerts.append(Alert(
            site_key=site["key"], rule="insecure_tls", severity=SEVERITY_CRITICAL,
            summary=f"min TLS {tls} — insecure",
            detail={"min_tls_version": tls},
        ))
    return alerts
