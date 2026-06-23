"""Tests for the Top 5xx Offenders panel + the Sites tab 5xx column.

Both pieces consume the same `cf.analytics.{requests_7d,requests_5xx_7d,
pct_5xx_7d,top_status_codes_7d}` block that the edge_5xx_rate rule already
relies on. The tests pin:

  - Volume floors match the alert engine (MIN_REQUESTS_7D=1000, MIN_5XX_EVENTS=10)
  - Sort order is pct descending
  - Severity classes match the alert engine thresholds (warn 1%, crit 3%)
  - Status-code breakdown filters to ONLY 5xx codes (no 200/301/403 noise)
  - Sites tab cell falls back to "—" cleanly when analytics absent
"""
from projects.fleet_monitoring.render import (
    _top_5xx_sites, _top_5xx_card, _sites_tab, _compact_num)


def _site(key, *, req=10_000, err=100, pct=None, codes=None):
    if pct is None and req > 0:
        pct = (err / req) * 100
    cf_an = {
        "requests_7d": req,
        "requests_5xx_7d": err,
        "pct_5xx_7d": pct,
        "top_status_codes_7d": codes or [{"code": 504, "requests": err}],
    }
    return {"key": key, "cf": {"analytics": cf_an}}


def test_top_5xx_respects_volume_floors():
    """Sites under either floor (req<1000 OR err<10) must NOT appear.
    Without this the panel would be dominated by tiny sites where 1 error
    inflates the rate. Mirrors edge_5xx_rate.evaluate exactly."""
    snap = {"sites": [
        _site("tiny.com", req=50, err=1, pct=2.0),       # below req floor
        _site("noise.com", req=5000, err=3, pct=0.06),   # below err floor
        _site("real.com", req=5000, err=50, pct=1.0),    # passes
    ]}
    top = _top_5xx_sites(snap, n=5)
    assert [r["key"] for r in top] == ["real.com"]


def test_top_5xx_sorted_by_pct_descending():
    snap = {"sites": [
        _site("a.com", req=10_000, err=100, pct=1.0),
        _site("c.com", req=10_000, err=400, pct=4.0),
        _site("b.com", req=10_000, err=200, pct=2.0),
    ]}
    top = _top_5xx_sites(snap, n=5)
    assert [r["key"] for r in top] == ["c.com", "b.com", "a.com"]


def test_top_5xx_truncates_to_n():
    sites = [_site(f"s{i}.com", req=10_000, err=100 + i, pct=1.0 + i)
             for i in range(10)]
    top = _top_5xx_sites({"sites": sites}, n=3)
    assert len(top) == 3
    # Top by pct -> s9, s8, s7
    assert [r["key"] for r in top] == ["s9.com", "s8.com", "s7.com"]


def test_top_5xx_card_renders_link_and_severity_pill():
    """Each row links to sites/<safe_key>.html and the % pill carries the
    matching severity class so the panel + alert engine agree on colour."""
    snap = {"sites": [
        _site("critical.com", req=10_000, err=400, pct=4.0),     # crit
        _site("warn.com", req=10_000, err=150, pct=1.5),         # warn
        _site("ok.com", req=10_000, err=50, pct=0.5),            # good
    ]}
    html = _top_5xx_card(snap)
    assert 'href="sites/critical.com.html"' in html
    assert "sev-critical" in html
    assert "sev-warning" in html
    # critical site renders before warn (sort order)
    assert html.index("critical.com") < html.index("warn.com")


def test_top_5xx_card_empty_state_when_no_qualifying_sites():
    """Healthy fleet (no site clears the floor) -> guidance text, not blank."""
    snap = {"sites": [_site("tiny.com", req=50, err=1, pct=2.0)]}
    html = _top_5xx_card(snap)
    assert "No sites cleared the volume floor" in html
    assert "1,000+ requests" in html


def test_top_5xx_card_filters_non_5xx_from_breakdown():
    """The top_status_codes_7d array carries every top code (200, 301, 403,
    504, etc.). The inline breakdown must show ONLY 5xx so the operator
    isn't distracted by the 200s that necessarily dominate."""
    snap = {"sites": [_site("x.com", req=10_000, err=500, pct=5.0, codes=[
        {"code": 200, "requests": 9000},
        {"code": 301, "requests": 400},
        {"code": 504, "requests": 300},
        {"code": 521, "requests": 200},
    ])]}
    html = _top_5xx_card(snap)
    # 5xx codes shown
    assert ">504=" in html and ">521=" in html
    # 2xx/3xx codes NOT shown in the breakdown spans
    assert ">200=" not in html
    assert ">301=" not in html


def test_sites_tab_renders_5xx_column_header():
    """The Sites table must gain a sortable 5xx column + tooltip header."""
    html = _sites_tab({"sites": []})
    assert ">5xx %<" in html
    assert "hover 5xx % for top status codes" in html


def test_sites_tab_5xx_cell_falls_back_to_dash_when_analytics_missing():
    """Sites without cf.analytics (e.g. WPE-only sites) must render '—'
    in the 5xx cell rather than crash on the missing key."""
    snap = {"sites": [{
        "key": "wpe-only.com",
        "wpe": {"account_name": "x", "bandwidth_gb_30d": 10},
        "cf": None,
    }]}
    html = _sites_tab(snap)
    assert "wpe-only.com" in html
    # Cell renders as a muted em-dash placeholder rather than throwing on
    # the missing analytics block. Em-dash is literal Unicode in the
    # rendered output, not the &mdash; entity.
    assert '<td class="num muted">—</td>' in html


def test_sites_tab_5xx_cell_carries_top_codes_in_title_tooltip():
    """Hover tooltip on the cell shows top 5xx codes so the operator can
    spot 504-dominant (timeout) vs 521-dominant (origin unreachable) sites
    without clicking through to the per-site page."""
    snap = {"sites": [{
        "key": "x.com",
        "wpe": {"bandwidth_gb_30d": 100},
        "cf": {"analytics": {
            "requests_7d": 10_000,
            "requests_5xx_7d": 400,
            "pct_5xx_7d": 4.0,
            "top_status_codes_7d": [
                {"code": 200, "requests": 9000},
                {"code": 504, "requests": 250},
                {"code": 521, "requests": 150},
            ],
        }},
    }]}
    html = _sites_tab(snap)
    assert "cell-pill sev-critical" in html  # 4% > CRIT_PCT of 3%
    # Title carries the top 5xx breakdown.
    assert 'title="504=250, 521=150"' in html


def test_compact_num_format():
    """Tight panel cells need compact counts: 1500 -> '1.5k'."""
    assert _compact_num(0) == "0"
    assert _compact_num(999) == "999"
    assert _compact_num(1_500) == "1.5k"
    assert _compact_num(17_753) == "17.8k"
    assert _compact_num(1_200_000) == "1.2M"
