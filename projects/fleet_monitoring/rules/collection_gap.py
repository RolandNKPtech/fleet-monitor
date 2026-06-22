"""Rule: self-monitoring — a site we expected data from returned nothing."""
from __future__ import annotations
from ..models import Alert, SEVERITY_WARNING

RULE_ID = "collection_gap"


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    alerts = []
    join = site.get("join_state", "")
    if "wpe" in join and site.get("wpe") is None:
        alerts.append(Alert(
            site_key=site["key"], rule=RULE_ID, severity=SEVERITY_WARNING,
            summary="WPE usage missing — collection gap",
            dedup_key="wpe",
            detail={"source": "wpe", "join_state": join},
        ))
    cf = site.get("cf") or {}
    cfg = cf.get("config") or {}
    if cf and "error" in cfg:
        alerts.append(Alert(
            site_key=site["key"], rule=RULE_ID, severity=SEVERITY_WARNING,
            summary=f"CF config fetch failed — collection gap ({cfg['error']})",
            dedup_key="cf_config",
            detail={"source": "cf_config", "error": cfg["error"]},
        ))
    return alerts
