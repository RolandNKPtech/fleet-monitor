from projects.fleet_monitoring.baselines import baseline, metric_history


def test_baseline_returns_none_below_minimum():
    assert baseline([10, 20, 30]) is None              # only 3 values, min is 7


def test_baseline_is_trailing_median():
    # 8 values — enough; median of last 14 (all 8) of [1..8] is 4.5
    assert baseline([1, 2, 3, 4, 5, 6, 7, 8]) == 4.5


def test_baseline_ignores_none_values():
    assert baseline([10, None, 10, 10, 10, 10, 10, None]) == 10


def test_metric_history_extracts_nested_path():
    history = [
        {"wpe": {"bandwidth_gb_30d": 100}},
        {"wpe": {"bandwidth_gb_30d": 110}},
        {"wpe": None},
    ]
    assert metric_history(history, ("wpe", "bandwidth_gb_30d")) == [100, 110, None]
