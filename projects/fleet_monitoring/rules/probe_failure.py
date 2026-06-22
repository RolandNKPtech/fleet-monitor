"""Rule: a probed site returned an unexpected status for some UA."""
from __future__ import annotations
from ..models import Alert, SEVERITY_WARNING

RULE_ID = "probe_failure"


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    probe = site.get("probe")
    if not probe:
        return []
    failures = [ua for ua, r in probe.items() if not r.get("ok")]
    if not failures:
        return []
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=SEVERITY_WARNING,
        summary=f"probe drift: {', '.join(failures)} returned unexpected codes",
        detail={"failures": {ua: probe[ua] for ua in failures}},
    )]
