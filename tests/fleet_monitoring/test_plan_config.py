from projects.fleet_monitoring.plan_config import (
    load_plans, account_is_configured, AccountPlan, primary_lookup_name)


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


def test_load_plans_registers_plan_under_each_alias(tmp_path, monkeypatch):
    """Sanitized YAML key (`acctA`) + real_account_names should make the
    same AccountPlan reachable under BOTH the alias and the real name."""
    import projects.fleet_monitoring.plan_config as pc
    f = tmp_path / "wpe-plans.yml"
    f.write_text(
        "accounts:\n"
        "  acctA:\n"
        "    real_account_names: [acct1real, acct1stg]\n"
        "    cycle_start_day: 13\n"
        "    bandwidth_gb_limit: 1700\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)

    plans = load_plans()
    assert set(plans.keys()) == {"acctA", "acct1real", "acct1stg"}
    # All three keys must point at the EXACT same AccountPlan instance — the
    # renderer dedupes on `id(plan)`, the alert engine dedupes on display_label.
    assert plans["acctA"] is plans["acct1real"] is plans["acct1stg"]
    assert plans["acctA"].display_label == "acctA"
    assert plans["acctA"].real_account_names == ["acct1real", "acct1stg"]


def test_load_plans_omitted_aliases_defaults_to_self(tmp_path, monkeypatch):
    """Back-compat: when real_account_names is absent, the YAML key IS the
    real name (matches every existing private-repo wpe-plans.yml)."""
    import projects.fleet_monitoring.plan_config as pc
    f = tmp_path / "wpe-plans.yml"
    f.write_text(
        "accounts:\n"
        "  acctA: {cycle_start_day: 13, bandwidth_gb_limit: 1700}\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)

    plans = load_plans()
    assert list(plans.keys()) == ["acctA"]
    assert plans["acctA"].display_label == "acctA"
    assert plans["acctA"].real_account_names == ["acctA"]


def test_primary_lookup_name_uses_first_alias_else_label():
    aliased = AccountPlan(display_label="acctA",
                          real_account_names=["acct1real", "acct1stg"])
    assert primary_lookup_name(aliased) == "acct1real"

    bare = AccountPlan(display_label="acct1real",
                       real_account_names=[])
    assert primary_lookup_name(bare) == "acct1real"
