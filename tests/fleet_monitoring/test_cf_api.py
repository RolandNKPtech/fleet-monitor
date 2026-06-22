import asyncio
from projects.fleet_monitoring.cf_api import parse_analytics_response, dns_proxy_state


def _day(date: str, requests: int, cached: int = 0, threats: int = 0,
         status_map: list | None = None) -> dict:
    return {
        "sum": {
            "requests": requests, "cachedRequests": cached, "threats": threats,
            "responseStatusMap": status_map or [],
        },
        "dimensions": {"date": date},
    }


def _wrap(groups: list[dict]) -> dict:
    return {"data": {"viewer": {"zones": [{"httpRequests1dGroups": groups}]}}}


def test_parse_analytics_response_sums_day_groups():
    raw = _wrap([
        _day("2026-05-14", 100, cached=60, threats=5),
        _day("2026-05-15", 200, cached=100, threats=15),
    ])
    a = parse_analytics_response(raw)
    assert a["requests_30d"] == 300
    assert a["threats"] == 20
    assert a["cache_hit_rate"] == 53.3   # 160/300


def test_parse_analytics_response_handles_empty():
    a = parse_analytics_response({})
    assert a["requests_30d"] == 0
    assert a["pct_5xx_30d"] == 0.0
    assert a["pct_5xx_7d"] == 0.0
    assert a["top_status_codes_7d"] == []


def test_parse_analytics_response_5xx_buckets_status_codes():
    raw = _wrap([_day("2026-05-15", 1000, status_map=[
        {"edgeResponseStatus": 200, "requests": 900},
        {"edgeResponseStatus": 404, "requests": 50},
        {"edgeResponseStatus": 500, "requests": 30},
        {"edgeResponseStatus": 522, "requests": 20},   # CF origin timeout
    ])])
    a = parse_analytics_response(raw)
    assert a["requests_5xx_30d"] == 50   # 30 + 20
    assert a["pct_5xx_30d"] == 5.0       # 50/1000


def test_parse_analytics_response_7d_window_rolls_off_old_5xx_day():
    # 8 calendar days, oldest spikes 100% 5xx — must roll off the 7d window.
    groups = [_day("2026-05-10", 100, status_map=[{"edgeResponseStatus": 500, "requests": 100}])]
    for i in range(7):
        d = f"2026-05-{11+i:02d}"
        groups.append(_day(d, 100, status_map=[{"edgeResponseStatus": 200, "requests": 100}]))
    a = parse_analytics_response(_wrap(groups))
    assert a["requests_30d"] == 800
    assert a["requests_5xx_30d"] == 100   # full 30d total still includes the spike
    assert a["requests_7d"] == 700
    assert a["requests_5xx_7d"] == 0      # but 7d window starts 2026-05-11
    assert a["pct_5xx_7d"] == 0.0


def test_parse_analytics_response_7d_window_anchors_on_calendar_not_list_size():
    # Sparse data: only 5 days of traffic spanning 12 calendar days.
    # The bug we're guarding against: list-slice grabs all 5 days, but the
    # date-anchored window must only include days within 6 days back from
    # the latest. With max_date=2026-05-25, cutoff=2026-05-19, only the
    # last two days (05-21, 05-25) qualify.
    groups = [
        _day("2026-05-14", 1000, status_map=[{"edgeResponseStatus": 500, "requests": 100}]),
        _day("2026-05-17", 1000, status_map=[{"edgeResponseStatus": 500, "requests": 100}]),
        _day("2026-05-19", 1000, status_map=[{"edgeResponseStatus": 500, "requests": 100}]),
        _day("2026-05-21", 1000, status_map=[{"edgeResponseStatus": 200, "requests": 1000}]),
        _day("2026-05-25", 1000, status_map=[{"edgeResponseStatus": 200, "requests": 1000}]),
    ]
    a = parse_analytics_response(_wrap(groups))
    # 30d totals span all 5 days
    assert a["requests_30d"] == 5000
    assert a["requests_5xx_30d"] == 300
    # 7d window is calendar-anchored: cutoff = 2026-05-25 - 6 = 2026-05-19
    # but 05-19 included (>= cutoff), 05-21 included, 05-25 included
    assert a["requests_7d"] == 3000
    assert a["requests_5xx_7d"] == 100   # only 05-19's 5xx survives
    # 05-14 and 05-17 (sub-cutoff) must NOT bleed into the 7d window
    assert a["pct_5xx_7d"] == round(100 / 3000 * 100, 2)


def test_parse_analytics_response_top_status_codes_7d_from_window_only():
    # Older days have a code distribution that must NOT pollute top_7d.
    old = [_day("2026-05-01", 1000, status_map=[
        {"edgeResponseStatus": 999, "requests": 1000},   # noise from way back
    ])]
    recent = [_day(f"2026-05-{20+i:02d}", 1000, status_map=[
        {"edgeResponseStatus": 200, "requests": 700},
        {"edgeResponseStatus": 404, "requests": 200},
        {"edgeResponseStatus": 500, "requests": 100},
    ]) for i in range(5)]
    a = parse_analytics_response(_wrap(old + recent))
    codes = [r["code"] for r in a["top_status_codes_7d"]]
    assert 999 not in codes   # old day must not appear in 7d codes
    assert codes[:3] == [200, 404, 500]


def test_parse_analytics_response_groups_out_of_order_get_sorted():
    # GraphQL doesn't guarantee order. The 7d window must still be the latest 7 dates.
    groups = [
        _day("2026-05-17", 100, status_map=[{"edgeResponseStatus": 200, "requests": 100}]),
        _day("2026-05-10", 100, status_map=[{"edgeResponseStatus": 500, "requests": 100}]),
        _day("2026-05-15", 100, status_map=[{"edgeResponseStatus": 200, "requests": 100}]),
    ]
    a = parse_analytics_response(_wrap(groups))
    # max_date=2026-05-17, cutoff=2026-05-11. 05-10 falls outside, 05-15 + 05-17 inside.
    assert a["requests_30d"] == 300
    assert a["requests_5xx_30d"] == 100    # full window
    assert a["requests_7d"] == 200          # 05-15 + 05-17 only
    assert a["requests_5xx_7d"] == 0        # 05-10's 5xx correctly excluded


def test_dns_proxy_state_reads_apex_and_www():
    records = [
        {"name": "example.com", "type": "A", "proxied": False},
        {"name": "www.example.com", "type": "CNAME", "proxied": True},
    ]
    apex, www = dns_proxy_state(records, "example.com")
    assert apex is False and www is True
