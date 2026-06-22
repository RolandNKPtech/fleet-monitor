"""Tests for cost.summarize — fleet-wide CF subscription projection."""
from projects.fleet_monitoring.cost import summarize, _monthly


def _site(plan):
    return {"key": "x.com", "cf": {"plan": plan} if plan else {}}


def _plan(name, price, frequency="monthly", currency="USD"):
    return {"name": name, "price": price, "frequency": frequency,
            "currency": currency}


def test_summarize_empty_snapshot():
    s = summarize({"sites": []})
    assert s["total_monthly_usd"] == 0.0
    assert s["zone_count_with_plan"] == 0
    assert s["zone_count_without_plan"] == 0
    assert s["by_plan"] == []


def test_summarize_sums_per_plan_correctly():
    snap = {"sites": [
        _site(_plan("Free Website", 0)),
        _site(_plan("Free Website", 0)),
        _site(_plan("Pro Website", 20)),
        _site(_plan("Pro Website", 20)),
        _site(_plan("Business Website", 200)),
    ]}
    s = summarize(snap)
    assert s["total_monthly_usd"] == 240.0   # 0+0+20+20+200
    assert s["zone_count_with_plan"] == 5
    by_name = {p["name"]: p for p in s["by_plan"]}
    assert by_name["Free Website"]["count"] == 2
    assert by_name["Pro Website"]["count"] == 2
    assert by_name["Pro Website"]["monthly_usd"] == 40.0
    assert by_name["Business Website"]["monthly_usd"] == 200.0


def test_summarize_orders_plans_by_cost_desc():
    snap = {"sites": [
        _site(_plan("Free Website", 0)),
        _site(_plan("Pro Website", 20)),
        _site(_plan("Business Website", 200)),
    ]}
    s = summarize(snap)
    names = [p["name"] for p in s["by_plan"]]
    assert names == ["Business Website", "Pro Website", "Free Website"]


def test_summarize_counts_sites_without_plan_field():
    snap = {"sites": [
        _site(_plan("Pro Website", 20)),
        _site(None),                 # cf present, no plan
        {"key": "y.com", "cf": None},  # no cf at all (wpe-only)
    ]}
    s = summarize(snap)
    assert s["zone_count_with_plan"] == 1
    # wpe-only sites are skipped entirely (no cf block to check)
    assert s["zone_count_without_plan"] == 1


def test_summarize_yearly_frequency_normalized_to_monthly():
    snap = {"sites": [
        _site(_plan("Enterprise Website", 1200, frequency="yearly")),
    ]}
    s = summarize(snap)
    assert s["total_monthly_usd"] == 100.0   # 1200 / 12


def test_summarize_quarterly_frequency_normalized():
    snap = {"sites": [
        _site(_plan("Custom", 60, frequency="quarterly")),
    ]}
    s = summarize(snap)
    assert s["total_monthly_usd"] == 20.0    # 60 / 3


def test_summarize_handles_missing_price():
    snap = {"sites": [_site(_plan("Free Website", None))]}
    s = summarize(snap)
    assert s["total_monthly_usd"] == 0.0
    assert s["zone_count_with_plan"] == 1


def test_summarize_flags_mixed_currency():
    snap = {"sites": [
        _site(_plan("Pro Website", 20, currency="USD")),
        _site(_plan("Pro Website", 18, currency="EUR")),
    ]}
    s = summarize(snap)
    assert s["currency"] == "mixed"


def test_summarize_handles_unparseable_price_safely():
    snap = {"sites": [_site(_plan("Weird Plan", "twenty bucks"))]}
    s = summarize(snap)
    assert s["total_monthly_usd"] == 0.0   # graceful, not crash


def test_monthly_normalizer_unknown_frequency_defaults_to_monthly():
    # Safer to over-project than silently zero out unknown cadence.
    assert _monthly(20, "fortnightly") == 20.0
