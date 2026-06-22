"""Tests for plan_changed rule."""
from projects.fleet_monitoring.rules import plan_changed


def _site(plan_name, price=None, frequency="monthly"):
    if plan_name is None:
        return {"key": "a.com", "cf": {}}
    return {"key": "a.com",
            "cf": {"plan": {"name": plan_name, "price": price,
                            "frequency": frequency, "currency": "USD"}}}


def test_fires_warning_on_upgrade():
    history = [_site("Free Website", 0)]
    alerts = plan_changed.evaluate(_site("Pro Website", 20), history)
    assert len(alerts) == 1
    assert alerts[0].severity == "warning"
    assert alerts[0].rule == "plan_changed"
    assert "upgrade" in alerts[0].summary
    assert "Free Website -> Pro Website" in alerts[0].summary


def test_fires_warning_on_downgrade():
    history = [_site("Pro Website", 20)]
    alerts = plan_changed.evaluate(_site("Free Website", 0), history)
    assert len(alerts) == 1
    assert "downgrade" in alerts[0].summary


def test_does_not_fire_when_plan_unchanged():
    history = [_site("Pro Website", 20)]
    assert plan_changed.evaluate(_site("Pro Website", 20), history) == []


def test_does_not_fire_on_first_observation_no_history():
    assert plan_changed.evaluate(_site("Pro Website", 20), []) == []


def test_does_not_fire_when_history_lacks_plan_info():
    # Older snapshots predating plan capture have no cf.plan — should skip.
    history = [_site(None), _site(None)]
    assert plan_changed.evaluate(_site("Pro Website", 20), history) == []


def test_skips_when_current_has_no_plan():
    history = [_site("Pro Website", 20)]
    assert plan_changed.evaluate(_site(None), history) == []


def test_picks_most_recent_prior_with_plan_info():
    # History oldest-first: Free, then no-plan gap, then current=Pro.
    # The rule must compare against the most recent prior with a plan.
    history = [_site("Free Website", 0), _site(None), _site(None)]
    alerts = plan_changed.evaluate(_site("Pro Website", 20), history)
    assert len(alerts) == 1
    assert "Free Website -> Pro Website" in alerts[0].summary


def test_detail_carries_old_new_price_and_direction():
    history = [_site("Business Website", 200)]
    alert = plan_changed.evaluate(_site("Enterprise Website", 5000), history)[0]
    assert alert.detail["old_plan"] == "Business Website"
    assert alert.detail["new_plan"] == "Enterprise Website"
    assert alert.detail["old_price"] == 200.0
    assert alert.detail["new_price"] == 5000.0
    assert alert.detail["direction"] == "upgrade"


def test_fingerprint_stable_for_lifecycle():
    history = [_site("Free Website", 0)]
    alert = plan_changed.evaluate(_site("Pro Website", 20), history)[0]
    assert alert.fingerprint() == "a.com:plan_changed"


def test_registered_in_rules_registry():
    from projects.fleet_monitoring import rules
    assert plan_changed in rules.REGISTRY
