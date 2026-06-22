"""Rule: bandwidth materially above the site's rolling baseline."""
from __future__ import annotations
from ..models import Alert, SEVERITY_WARNING, SEVERITY_CRITICAL
from ..baselines import baseline, metric_history

RULE_ID = "bandwidth_spike"
SPIKE_FACTOR = 1.4
ABSOLUTE_FLOOR_GB = 20.0


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    wpe = site.get("wpe") or {}
    current = wpe.get("bandwidth_gb_30d")
    if current is None or current < ABSOLUTE_FLOOR_GB:
        return []
    base = baseline(metric_history(history, ("wpe", "bandwidth_gb_30d")))
    if base is None or base <= 0:
        return []
    if current <= base * SPIKE_FACTOR:
        return []
    ratio = current / base
    severity = SEVERITY_CRITICAL if ratio >= 2.0 else SEVERITY_WARNING
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=severity,
        summary=f"bandwidth {current:.0f} GB vs baseline {base:.0f} GB (+{(ratio-1)*100:.0f}%)",
        detail={"current_gb": current, "baseline_gb": round(base, 1),
                "ratio": round(ratio, 2)},
    )]
