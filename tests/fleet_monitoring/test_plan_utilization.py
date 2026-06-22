from datetime import date
from projects.fleet_monitoring.plan_config import AccountPlan
from projects.fleet_monitoring.plan_utilization import evaluate_plan_utilization


def _daily(account, date_str, total_bytes, billable=0):
    return {"date": date_str, "account": account, "install": "x",
            "network_total_bytes": total_bytes, "billable_visits": billable}


def test_no_alerts_for_unconfigured_accounts():
    plans = {"acctA": AccountPlan()}  # all-null
    daily = [_daily("acctA", "2026-05-19", 9_999_999_999_999)]
    assert evaluate_plan_utilization(date(2026, 5, 19), plans, daily) == []


def test_critical_when_cycle_to_date_meets_95pct():
    # 95 GB used of 100 GB plan = 95% — critical
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    daily = [_daily("x", "2026-05-13", 95_000_000_000)]
    alerts = evaluate_plan_utilization(date(2026, 5, 19), plans, daily)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.rule == "plan_utilization"
    assert a.severity == "critical"
    assert a.site_key == "x"
    assert a.detail["pct_used"] >= 95
    assert a.detail["axis"] == "bandwidth"


def test_warning_at_80pct():
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    daily = [_daily("x", "2026-05-13", 80_000_000_000)]
    alerts = evaluate_plan_utilization(date(2026, 5, 19), plans, daily)
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"


def test_no_alert_below_80pct():
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    daily = [_daily("x", "2026-05-13", 50_000_000_000)]
    assert evaluate_plan_utilization(date(2026, 5, 19), plans, daily) == []


def test_projection_alert_when_extrapolation_exceeds_limit():
    """7 days into a 31-day cycle, used 50 GB of a 100 GB plan.
    Linear projection: 50 / 7 × 31 = 221 GB → 221% projected → projection alert.
    pct_used = 50% so neither 80% nor 95% would trigger."""
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    daily = [_daily("x", f"2026-05-{d:02d}", 7_142_857_142)
             for d in range(13, 20)]   # 7 days × ~7.14 GB = 50 GB
    alerts = evaluate_plan_utilization(date(2026, 5, 19), plans, daily)
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].detail["kind"] == "projection"
    assert alerts[0].detail["projected_pct"] > 100


def test_critical_wins_over_projection_for_same_account():
    """When both 95% used AND projection > 100%, emit only the critical."""
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    daily = [_daily("x", "2026-05-13", 96_000_000_000)]   # 96% used
    alerts = evaluate_plan_utilization(date(2026, 5, 19), plans, daily)
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"


def test_single_day_high_usage_suppresses_projection_alert():
    """1 day of data, 50% used → cycle-to-date below threshold AND projection
    suppressed (MIN_PROJECTION_DAYS=2). No alert. A single-day spike is not
    a trendline — extrapolating from one observation would be a made-up number.
    """
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    # today is 2026-05-19, cycle_start_day=13 → day_n=7, but only 1 row of data
    daily = [_daily("x", "2026-05-19", 50_000_000_000)]  # 50 GB on day 7
    assert evaluate_plan_utilization(date(2026, 5, 19), plans, daily) == []


def test_single_day_critical_still_fires():
    """data_days=1 suppresses PROJECTION alerts, NOT severity-threshold alerts.
    95%+ cycle-to-date is observed fact, not extrapolation — must still fire.
    """
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    daily = [_daily("x", "2026-05-19", 96_000_000_000)]  # 96 GB on day 7
    alerts = evaluate_plan_utilization(date(2026, 5, 19), plans, daily)
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"
    assert alerts[0].detail["kind"] == "threshold"


def test_visits_axis_emits_distinct_alert():
    """When visits also configured, the visit-axis alert coexists with bandwidth."""
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100,
                               visits_limit=1000)}
    daily = [_daily("x", "2026-05-13", 96_000_000_000, billable=960)]
    alerts = evaluate_plan_utilization(date(2026, 5, 19), plans, daily)
    axes = sorted(a.detail["axis"] for a in alerts)
    assert axes == ["bandwidth", "visits"]
    assert all(a.severity == "critical" for a in alerts)
    # Distinct fingerprints so lifecycle tracks them separately.
    assert alerts[0].fingerprint() != alerts[1].fingerprint()
