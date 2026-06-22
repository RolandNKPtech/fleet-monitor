from datetime import date
from projects.fleet_monitoring.plan_config import AccountPlan
from projects.fleet_monitoring.plan_utilization import evaluate_plan_utilization
from projects.fleet_monitoring.render import _plan_utilization_panel


def _daily(account, date_str, total_bytes, billable=0):
    return {"date": date_str, "account": account, "install": "x",
            "network_total_bytes": total_bytes, "billable_visits": billable}


def test_panel_shows_configured_account_with_pct_and_projection():
    plans = {"acctF": AccountPlan(cycle_start_day=13,
                                          bandwidth_gb_limit=1700)}
    daily = [_daily("acctF", "2026-05-13", 100_000_000_000),
             _daily("acctF", "2026-05-14", 100_000_000_000),
             _daily("acctF", "2026-05-19", 87_000_000_000)]
    html = _plan_utilization_panel(today=date(2026, 5, 19),
                                   plans=plans, daily_rows=daily,
                                   snapshot={"sites": []})
    assert "Plan Utilization" in html
    assert "acctF" in html
    assert "of 1,700 GB" in html               # the configured limit shows
    assert "% cycle-to-date" in html or "%" in html
    assert "cycle 2026-05-13" in html.lower()  # cycle window labeled
    assert "wpe-plans.yml" in html              # source-of-truth label


def test_panel_shows_unconfigured_account_with_call_to_action():
    """When plan limit is null, panel shows current GB but no % or projection."""
    plans = {"acctD": AccountPlan()}
    daily = []
    html = _plan_utilization_panel(today=date(2026, 5, 19),
                                   plans=plans, daily_rows=daily,
                                   snapshot={"sites": [
                                       {"wpe": {"account_name": "acctD",
                                                "bandwidth_gb_30d": 956.0}}]})
    assert "acctD" in html
    assert "not set" in html or "configure" in html.lower()
    assert "956" in html                      # current consumption surfaces
    assert "%" not in html.split("not set")[0].split("acctD")[1]


def test_panel_renders_with_zero_accounts_configured():
    """Spec §14 decision: always show the panel; zero-config = placeholder row."""
    html = _plan_utilization_panel(today=date(2026, 5, 19),
                                   plans={}, daily_rows=[],
                                   snapshot={"sites": []})
    assert "Plan Utilization" in html
    assert "wpe-plans.yml" in html.lower() or "configure" in html.lower()


def test_panel_shows_real_account_name_not_sanitized_alias():
    """An aliased plan (acctA -> realacct) must render the REAL WPE
    account name as the card title. The alias exists only to keep the
    public source repo client-free; operators need the real name on the
    rendered dashboard so they can match a card to the actual WPE billing
    portal / SSH host.

    Regression guard: this is exactly the "what server is acctA?" confusion
    the operator hit on the live dashboard."""
    plan = AccountPlan(display_label="acctA",
                       real_account_names=["realacct"])
    # Same plan registered under both keys, as load_plans() does.
    plans = {"acctA": plan, "realacct": plan}
    html = _plan_utilization_panel(today=date(2026, 5, 19),
                                   plans=plans, daily_rows=[],
                                   snapshot={"sites": [
                                       {"wpe": {"account_name": "realacct",
                                                "bandwidth_gb_30d": 674.0}}]})
    # The real name appears in the card title.
    assert ">realacct<" in html
    # The sanitized alias must NOT appear as a card title.
    assert ">acctA<" not in html
    # The current 30-day rolling still shows (rolling-by-account join uses alias).
    assert "674" in html


def test_panel_includes_cost_line_when_overage_rate_configured_and_overage_positive():
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100,
                               overage_per_gb_usd=0.30)}
    # 50 GB used in 7 days → projection ~ 221 GB → ~121 GB over plan.
    daily = [_daily("x", f"2026-05-{d:02d}", 7_142_857_142)
             for d in range(13, 20)]
    html = _plan_utilization_panel(today=date(2026, 5, 19),
                                   plans=plans, daily_rows=daily,
                                   snapshot={"sites": []})
    assert "$" in html


def test_panel_projection_matches_analyzer_projection():
    """The number an operator reads in the dashboard MUST equal what the alert
    engine computes. If the panel says 'proj 150%' but the alert says 'proj 221%',
    the operator can't trust either. Regression test for the data_days/day_n
    denominator divergence the final code review flagged.
    """
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    # 7 days of data → projection should be ~221% (50 GB / 7 days × 31 days = 221 GB)
    daily = [_daily("x", f"2026-05-{d:02d}", 7_142_857_142)
             for d in range(13, 20)]
    today = date(2026, 5, 19)
    alerts = evaluate_plan_utilization(today, plans, daily)
    proj_alert = next(a for a in alerts if a.detail.get("kind") == "projection")
    alert_pct = proj_alert.detail["projected_pct"]
    html = _plan_utilization_panel(today=today, plans=plans, daily_rows=daily,
                                   snapshot={"sites": []})
    # Panel renders projection rounded to nearest integer percent
    expected_panel_str = f"proj {round(alert_pct):.0f}%"
    assert expected_panel_str in html, (
        f"Panel projection diverges from alert. Alert says "
        f"projected_pct={alert_pct}, expected panel substring "
        f"{expected_panel_str!r} not found in HTML.")


def test_panel_suppresses_projection_when_only_one_data_day():
    """Single-day data → panel shows 'projection unavailable' instead of
    a spurious projection bar.  Mirrors the analyzer's MIN_PROJECTION_DAYS
    suppression so operator + alert engine agree there's no trendline yet.
    """
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100)}
    daily = [_daily("x", "2026-05-19", 50_000_000_000)]
    html = _plan_utilization_panel(today=date(2026, 5, 19), plans=plans,
                                   daily_rows=daily, snapshot={"sites": []})
    assert "projection unavailable" in html or "need 2+ data days" in html
    assert "proj " not in html.split("cycle-to-date")[1]   # no projection % rendered


def test_panel_omits_cost_line_when_overage_rate_not_configured():
    plans = {"x": AccountPlan(cycle_start_day=13, bandwidth_gb_limit=100,
                               overage_per_gb_usd=None)}
    daily = [_daily("x", f"2026-05-{d:02d}", 7_142_857_142)
             for d in range(13, 20)]
    html = _plan_utilization_panel(today=date(2026, 5, 19),
                                   plans=plans, daily_rows=daily,
                                   snapshot={"sites": []})
    assert "$" not in html
