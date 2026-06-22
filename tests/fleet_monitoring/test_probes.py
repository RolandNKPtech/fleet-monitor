from projects.fleet_monitoring.probes import select_probe_targets


def test_select_probe_targets_includes_all_managed_plus_rotating_sample():
    roster = [{"key": f"site{i}.com"} for i in range(100)]
    managed = {"site3.com", "site7.com"}
    # day 0 of rotation
    day0 = select_probe_targets(roster, managed, day_index=0, sample_size=10)
    assert "site3.com" in day0 and "site7.com" in day0   # managed always probed
    assert len(day0) == 12                                # 2 managed + 10 sampled
    # a later rotation day picks a different unmanaged slice
    day1 = select_probe_targets(roster, managed, day_index=1, sample_size=10)
    unmanaged_day0 = set(day0) - managed
    unmanaged_day1 = set(day1) - managed
    assert unmanaged_day0 != unmanaged_day1
