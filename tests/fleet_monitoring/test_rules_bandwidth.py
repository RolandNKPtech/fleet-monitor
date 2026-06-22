from projects.fleet_monitoring.rules import bandwidth_spike, mb_per_visit_high, bot_ratio


def _hist(bw_values):
    return [{"wpe": {"bandwidth_gb_30d": v}} for v in bw_values]


def test_bandwidth_spike_fires_above_baseline_and_floor():
    site = {"key": "x.com", "wpe": {"bandwidth_gb_30d": 90}}
    history = _hist([40] * 8)                 # baseline 40 → 90 > 40*1.4 and > 20
    alerts = bandwidth_spike.evaluate(site, history)
    assert len(alerts) == 1
    assert alerts[0].rule == "bandwidth_spike"
    assert alerts[0].severity in ("warning", "critical")


def test_bandwidth_spike_silent_below_absolute_floor():
    site = {"key": "tiny.com", "wpe": {"bandwidth_gb_30d": 5}}
    history = _hist([1] * 8)                   # 5 > 1*1.4 but < 20 GB floor
    assert bandwidth_spike.evaluate(site, history) == []


def test_bandwidth_spike_silent_without_baseline():
    site = {"key": "new.com", "wpe": {"bandwidth_gb_30d": 90}}
    assert bandwidth_spike.evaluate(site, _hist([40] * 3)) == []   # < 7 snapshots


def test_mb_per_visit_high_fires_over_threshold():
    site = {"key": "x.com", "wpe": {"mb_per_visit": 47, "bandwidth_gb_30d": 50}}
    alerts = mb_per_visit_high.evaluate(site, [])
    assert len(alerts) == 1 and alerts[0].rule == "mb_per_visit_high"


def test_bot_ratio_fires_on_low_billable_share():
    site = {"key": "x.com",
            "wpe": {"billable_visits_30d": 200, "total_visits_30d": 20000,
                    "bandwidth_gb_30d": 80}}
    alerts = bot_ratio.evaluate(site, [])
    assert len(alerts) == 1 and alerts[0].rule == "bot_ratio"
