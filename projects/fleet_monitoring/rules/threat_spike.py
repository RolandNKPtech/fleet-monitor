"""Rule: CF 30-day threat count rising sharply vs the site's own baseline.

Complements `tracking_failure` (which catches a tracking break) by surfacing
active-attack pressure. The signal is cumulative — a 1.5x lift in the rolling
30-day window means the last week pulled in roughly as many threats as the
previous month. Combined with the alert lifecycle, that surfaces sustained
surges rather than single-day blips."""
from __future__ import annotations
from ..models import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING
from ..baselines import baseline, metric_history

RULE_ID = "threat_spike"
MIN_THREATS = 500       # quiet sites can wiggle by orders of magnitude — ignore
WARN_RATIO = 1.5        # current >= 1.5x baseline
CRIT_RATIO = 2.0        # current >= 2.0x baseline


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    cf_an = (site.get("cf") or {}).get("analytics") or {}
    current = cf_an.get("threats")
    if current is None or current < MIN_THREATS:
        return []
    base = baseline(metric_history(history, ("cf", "analytics", "threats")))
    if base is None or base <= 0:
        return []
    ratio = current / base
    if ratio < WARN_RATIO:
        return []
    severity = SEVERITY_CRITICAL if ratio >= CRIT_RATIO else SEVERITY_WARNING
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=severity,
        summary=(f"threats 30d {int(current):,} vs baseline {base:,.0f} "
                 f"(+{(ratio-1)*100:.0f}%) — sustained attack pressure"),
        detail={"threats_30d": int(current), "baseline": round(base, 1),
                "ratio": round(ratio, 2)},
    )]
