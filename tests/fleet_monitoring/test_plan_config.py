from projects.fleet_monitoring.plan_config import (
    load_plans, account_is_configured, AccountPlan, primary_lookup_name)


def test_load_plans_returns_account_plans(tmp_path, monkeypatch):
    import projects.fleet_monitoring.plan_config as pc
    f = tmp_path / "wpe-plans.yml"
    f.write_text(
        "accounts:\n"
        "  nkpmedical1: {cycle_start_day: 13, bandwidth_gb_limit: 1700, "
        "visits_limit: null, overage_per_gb_usd: 0.30}\n"
        "  nkpmedical2: {cycle_start_day: null, bandwidth_gb_limit: null, "
        "visits_limit: null, overage_per_gb_usd: null}\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)

    plans = load_plans()
    assert set(plans.keys()) == {"nkpmedical1", "nkpmedical2"}
    p1 = plans["nkpmedical1"]
    assert isinstance(p1, AccountPlan)
    assert p1.cycle_start_day == 13
    assert p1.bandwidth_gb_limit == 1700
    assert p1.visits_limit is None
    assert p1.overage_per_gb_usd == 0.30

    p2 = plans["nkpmedical2"]
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
        "    real_account_names: [nkpmedical1, nkpmedical1stg]\n"
        "    cycle_start_day: 13\n"
        "    bandwidth_gb_limit: 1700\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)

    plans = load_plans()
    assert set(plans.keys()) == {"acctA", "nkpmedical1", "nkpmedical1stg"}
    # All three keys must point at the EXACT same AccountPlan instance — the
    # renderer dedupes on `id(plan)`, the alert engine dedupes on display_label.
    assert plans["acctA"] is plans["nkpmedical1"] is plans["nkpmedical1stg"]
    assert plans["acctA"].display_label == "acctA"
    assert plans["acctA"].real_account_names == ["nkpmedical1", "nkpmedical1stg"]


def test_load_plans_omitted_aliases_defaults_to_self(tmp_path, monkeypatch):
    """Back-compat: when real_account_names is absent, the YAML key IS the
    real name (matches every existing private-repo wpe-plans.yml)."""
    import projects.fleet_monitoring.plan_config as pc
    f = tmp_path / "wpe-plans.yml"
    f.write_text(
        "accounts:\n"
        "  nkpmedical1: {cycle_start_day: 13, bandwidth_gb_limit: 1700}\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)

    plans = load_plans()
    assert list(plans.keys()) == ["nkpmedical1"]
    assert plans["nkpmedical1"].display_label == "nkpmedical1"
    assert plans["nkpmedical1"].real_account_names == ["nkpmedical1"]


def test_primary_lookup_name_uses_first_alias_else_label():
    aliased = AccountPlan(display_label="acctA",
                          real_account_names=["nkpmedical1", "nkpmedical1stg"])
    assert primary_lookup_name(aliased) == "nkpmedical1"

    bare = AccountPlan(display_label="nkpmedical1",
                       real_account_names=[])
    assert primary_lookup_name(bare) == "nkpmedical1"


def test_load_plans_fetches_live_bandwidth_when_yaml_field_is_null(
        tmp_path, monkeypatch):
    """fetch_live_limits=True must call wpe_api.get_account_limits and fill
    any null bandwidth_gb_limit / visits_limit from /accounts/{id}/limits.
    YAML values must NOT be overridden — operator-set values always win."""
    import projects.fleet_monitoring.plan_config as pc
    f = tmp_path / "wpe-plans.yml"
    f.write_text(
        "accounts:\n"
        "  acctA:\n"
        "    real_account_names: [nkpmedical1]\n"
        "    cycle_start_day: 13\n"
        # bandwidth_gb_limit + visits_limit OMITTED -> should be filled
        "  acctB:\n"
        "    real_account_names: [nkpmedical2]\n"
        "    cycle_start_day: 1\n"
        "    bandwidth_gb_limit: 500\n"        # YAML override -> kept
        "    visits_limit: 100000\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)
    # Session conftest stubs `_wpe_credentials_present` to False so the rest
    # of the suite stays fast. This test exercises the fetch path, so undo
    # the stub locally — env + lambda both need to look like creds exist.
    monkeypatch.setenv("WPE_API_USER", "fake")
    monkeypatch.setenv("WPE_API_PASSWORD", "fake")
    monkeypatch.setattr(pc, "_wpe_credentials_present", lambda: True)

    # Stub the live wpe_api so the test doesn't hit the network.
    class FakeWpe:
        @staticmethod
        def list_accounts():
            return [{"id": "uuid-1", "name": "nkpmedical1"},
                    {"id": "uuid-2", "name": "nkpmedical2"}]
        @staticmethod
        def get_account_limits(acct_id):
            return {"uuid-1": {"bandwidth": 1000, "visitors": None,
                               "storage": 300},
                    "uuid-2": {"bandwidth": 999, "visitors": 50000,
                               "storage": 100}}.get(acct_id)
    monkeypatch.setattr(
        "projects.fleet_monitoring.wpe_api.list_accounts",
        FakeWpe.list_accounts)
    monkeypatch.setattr(
        "projects.fleet_monitoring.wpe_api.get_account_limits",
        FakeWpe.get_account_limits)

    plans = load_plans(fetch_live_limits=True)

    # acctA had nothing in YAML -> live values fill in.
    assert plans["acctA"].bandwidth_gb_limit == 1000.0
    # visitors=None from API means unlimited; loader leaves visits_limit None.
    assert plans["acctA"].visits_limit is None

    # acctB had YAML values -> kept, NOT clobbered by the live 999.
    assert plans["acctB"].bandwidth_gb_limit == 500
    assert plans["acctB"].visits_limit == 100000


def test_load_plans_skips_live_fetch_when_credentials_missing(
        tmp_path, monkeypatch):
    """No WPE creds -> no network call, plans stay as the YAML declared.
    Keeps developer machines + test runs deterministic."""
    import projects.fleet_monitoring.plan_config as pc
    f = tmp_path / "wpe-plans.yml"
    f.write_text(
        "accounts:\n"
        "  acctA: {real_account_names: [x], cycle_start_day: 1}\n",
        encoding="utf-8")
    monkeypatch.setattr(pc, "PLAN_FILE", f)
    monkeypatch.delenv("WPE_API_USER", raising=False)
    monkeypatch.delenv("WPE_API_PASSWORD", raising=False)

    # If the fetch path were taken anyway, this would raise.
    def boom(*_args, **_kw):
        raise AssertionError("Should not be called when creds are missing")
    monkeypatch.setattr(
        "projects.fleet_monitoring.wpe_api.list_accounts", boom)

    plans = load_plans(fetch_live_limits=True)
    assert plans["acctA"].bandwidth_gb_limit is None  # still null
