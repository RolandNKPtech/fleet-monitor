"""Plan-utilization analyzer — emits ONE alert per account per axis per run.

Runs once per analyze pass (not per site), consumes `data/daily.jsonl` rows
plus the wpe-plans.yml config, and uses cycle.py for the cycle math.
Three-tier severity per axis (bandwidth, visits): warning at 80% of plan
used cycle-to-date, critical at 95%, warning ("projection") when the linear
extrapolation through the rest of the cycle exceeds 100% — but only when
the cycle-to-date hasn't already hit 95% (the critical already covers it).

Accounts whose AccountPlan isn't configured (cycle_start_day or limit None)
emit zero alerts on that axis — there is no honest computation possible.
"""
from __future__ import annotations
from datetime import date

from .cycle import cycle_window, cycle_to_date_gb, cycle_to_date_visits
from .models import Alert, SEVERITY_CRITICAL, SEVERITY_WARNING
from .plan_config import AccountPlan, account_is_configured

RULE_ID = "plan_utilization"
WARN_PCT = 80.0
CRIT_PCT = 95.0
# Below this many observed data points, projection is suppressed everywhere
# (analyzer AND render panel) — a single-day reading isn't a trendline.
MIN_PROJECTION_DAYS = 2


def count_data_days(account: str, daily_rows: list[dict],
                    cycle_start: date, today: date) -> int:
    """Count distinct calendar days with data for `account` in [cycle_start, today].

    Public — render.py imports this so the dashboard's projection display
    uses the same denominator as the alert engine.  Diverging denominators
    would mean operators see one number in the panel and another in alerts.
    """
    days: set[str] = set()
    for r in daily_rows:
        if r.get("account") != account:
            continue
        date_str = r.get("date", "")
        try:
            row_date = date.fromisoformat(date_str)
        except ValueError:
            continue
        if cycle_start <= row_date <= today:
            days.add(date_str)
    return len(days)


def _eval_axis(account: str, plan: AccountPlan, axis: str,
               used: float, limit: float | None,
               day_n: int, cycle_length: int,
               data_days: int = 0) -> Alert | None:
    """Decide which (if any) alert to emit for one axis of one account.

    `data_days` is the number of distinct calendar days with data for this
    account in the current cycle window.  Projection extrapolates ONLY from
    observed days (never assumes zeros on missing days — that would be a
    made-up number) and requires at least MIN_PROJECTION_DAYS observations.
    """
    if limit is None or limit <= 0:
        return None
    pct_used = (used / limit) * 100
    projection_active = data_days >= MIN_PROJECTION_DAYS
    projected = (used / data_days) * cycle_length if projection_active else 0.0
    projected_pct = (projected / limit) * 100 if projection_active else 0.0

    if pct_used >= CRIT_PCT:
        sev = SEVERITY_CRITICAL
        kind = "threshold"
        summary = (
            f"{account} {axis} {used:.0f} of {limit:.0f} plan "
            f"({pct_used:.1f}%) — critical")
    elif pct_used >= WARN_PCT:
        sev = SEVERITY_WARNING
        kind = "threshold"
        summary = (
            f"{account} {axis} {used:.0f} of {limit:.0f} plan "
            f"({pct_used:.1f}%) — heads up")
    elif projected_pct > 100:
        sev = SEVERITY_WARNING
        kind = "projection"
        summary = (
            f"{account} {axis} projected {projected:.0f} "
            f"({projected_pct:.0f}% of plan) — track to bust before cycle end")
    else:
        return None

    return Alert(
        site_key=account, rule=RULE_ID, severity=sev,
        summary=summary,
        dedup_key=axis,
        detail={
            "axis": axis, "kind": kind,
            "used": round(used, 2), "limit": float(limit),
            "pct_used": round(pct_used, 2),
            "projected": round(projected, 2),
            "projected_pct": round(projected_pct, 2),
            "day_n": day_n, "cycle_length": cycle_length,
        },
    )


def evaluate_plan_utilization(today: date,
                              plans: dict[str, AccountPlan],
                              daily_rows: list[dict]) -> list[Alert]:
    """Account-keyed alerts for plan utilization, both bandwidth + visits.

    Returns at most TWO alerts per account: one for the bandwidth axis,
    one for the visits axis (each axis evaluated independently so they
    can carry distinct severities and fingerprints).
    """
    alerts: list[Alert] = []
    for account, plan in plans.items():
        if not account_is_configured(plan):
            continue
        cycle_start, cycle_end, day_n, cycle_length = cycle_window(
            today, plan.cycle_start_day)

        data_days = count_data_days(account, daily_rows, cycle_start, today)

        used_gb = cycle_to_date_gb(account, daily_rows, cycle_start, today)
        bw_alert = _eval_axis(account, plan, "bandwidth",
                              used_gb, plan.bandwidth_gb_limit,
                              day_n, cycle_length, data_days)
        if bw_alert:
            alerts.append(bw_alert)

        if plan.visits_limit is not None:
            used_visits = cycle_to_date_visits(
                account, daily_rows, cycle_start, today)
            v_alert = _eval_axis(account, plan, "visits",
                                 used_visits, plan.visits_limit,
                                 day_n, cycle_length, data_days)
            if v_alert:
                alerts.append(v_alert)

    return alerts
