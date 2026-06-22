from projects.fleet_monitoring.plan_config import (
    load_plans, account_is_configured, AccountPlan)


def test_load_plans_returns_account_plans(tmp_path, monkeypatch):
    import projects.fleet_monitoring.plan_config as pc
    f = tmp_path / "wpe-plans.yml"
    f.write_text(
        "accounts:\n"
        "  acctA: {cycle_start_day: 13, bandwidth_gb_limit: 1700, "
        "visits_limit: null, overage_per_gb_usd: 0.30}\n"
        "  acctB: {cycle_start_day: null, bandwidth_gb_limit: null, "
        "visits_limit: null, overage_per_gb_usd: null}\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)

    plans = load_plans()
    assert set(plans.keys()) == {"acctA", "acctB"}
    p1 = plans["acctA"]
    assert isinstance(p1, AccountPlan)
    assert p1.cycle_start_day == 13
    assert p1.bandwidth_gb_limit == 1700
    assert p1.visits_limit is None
    assert p1.overage_per_gb_usd == 0.30

    p2 = plans["acctB"]
    assert p2.cycle_start_day is None
    assert p2.bandwidth_gb_limit is None


def test_load_plans_returns_empty_when_file_missing(tmp_path, monkeypatch):
    import projects.fleet_monitoring.plan_config as pc
    monkeypatch.setattr(pc, "PLAN_FILE", tmp_path / "missing.yml")
    assert load_plans() == {}


def test_account_is_configured_requires_both_cycle_and_limit():
    assert not account_is_configured(AccountPlan())
    assert not account_is_configured(AccountPlan(cycle_start_day=13))
    assert not account_is_configured(AccountPlan(bandwidth_gb_limit=1700))
    assert account_is_configured(
        AccountPlan(cycle_start_day=13, bandwidth_gb_limit=1700))
