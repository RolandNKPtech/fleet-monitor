"""Alert rule registry. Each module exposes RULE_ID and evaluate(site, history).

`bot_ratio` (legacy heuristic based on WPE billable visits) is intentionally
NOT in the REGISTRY — `bot_ratio_real` (GA4 sessions vs CF requests) replaces
it. The module file remains importable in case the heuristic ever needs to
be revived for a quick A/B test."""
from __future__ import annotations
from . import (bandwidth_spike, mb_per_visit_high, bot_ratio_real,
               config_drift, hard_thresholds, probe_failure, new_offender,
               fix_regression, collection_gap, tracking_failure,
               organic_traffic_drop, conversion_drop, threat_spike,
               edge_5xx_rate, cert_expiry, cache_hit_low, plan_changed)

REGISTRY = [bandwidth_spike, mb_per_visit_high, bot_ratio_real,
            config_drift, hard_thresholds, probe_failure, new_offender,
            fix_regression, collection_gap, tracking_failure,
            organic_traffic_drop, conversion_drop, threat_spike,
            edge_5xx_rate, cert_expiry, cache_hit_low, plan_changed]


def run_all(site: dict, history: list[dict],
            rule_changes: list[dict] | None = None) -> list:
    """Run every registered rule against one site. Returns a flat list of Alerts.

    `rule_changes` is forwarded only to config_drift (the lone rule that consumes
    them) so analyze.py can keep one clean call instead of iterating manually.
    """
    alerts = []
    for module in REGISTRY:
        if module is config_drift and rule_changes is not None:
            alerts.extend(module.evaluate(site, history, rule_changes=rule_changes))
        else:
            alerts.extend(module.evaluate(site, history))
    return alerts
