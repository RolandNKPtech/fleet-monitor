"""Tests for the Overview tab's Needs Attention panel.

The panel shows the top 8 NEW alerts as a compact list. Each per-site
alert's site name must be a clickable link to the per-site detail page
(sites/<safe_key>.html) so an operator can drill in with one click.
Fleet-level alerts (site_key="fleet" — e.g. analytics_token_failure)
stay as plain text because no per-site page exists for them.
"""
from projects.fleet_monitoring.render import _needs_attention


def _alert(site_key, rule, severity="warning", summary="x"):
    return {"site_key": site_key, "rule": rule, "severity": severity,
            "summary": summary, "state": "new"}


def test_per_site_alert_renders_as_link_to_site_page():
    snap = {"alerts": [_alert("dssrisk.com", "collection_gap",
                              summary="WPE usage missing")]}
    html = _needs_attention(snap)
    # The site name is wrapped in an <a> pointing at the per-site detail page.
    # safe_key preserves dots (allowed in filenames), only lowercases + strips
    # filesystem-unsafe chars — so dssrisk.com -> dssrisk.com.html.
    assert 'href="sites/dssrisk.com.html"' in html
    assert '<a class="alert-site"' in html
    assert "dssrisk.com</a>" in html


def test_fleet_level_alert_stays_plain_text():
    """analytics_token_failure uses site_key='fleet' and there is no per-site
    page for 'fleet' — wrapping it in <a> would 404 the operator. Render as
    plain span instead."""
    snap = {"alerts": [_alert("fleet", "analytics_token_failure",
                              severity="critical",
                              summary="ga4_pull failed")]}
    html = _needs_attention(snap)
    assert '<span class="alert-site">fleet</span>' in html
    assert 'href="sites/fleet.html"' not in html


def test_link_uses_safe_key_for_lowercasing_and_unsafe_chars():
    """safe_key lowercases the key and replaces filesystem-unsafe chars with
    hyphens (allows [a-z0-9.-] through). Uppercase or weird-char keys must
    therefore produce a different filename than the raw site_key."""
    snap = {"alerts": [_alert("Plastic Surgery North!", "bandwidth_spike")]}
    html = _needs_attention(snap)
    # Spaces + "!" -> hyphens; uppercase -> lowercase. Dots preserved.
    assert 'href="sites/plastic-surgery-north-.html"' in html


def test_alert_row_shows_wpe_account_chip_when_snapshot_carries_it():
    """Each alert row gets a small monospace chip showing which WPE server
    the site lives on (nkpmedical1-6) so the operator can scan the panel
    and immediately see 'is this on the box that's at capacity?' without
    clicking through to the site page.

    The chip is populated from snapshot.sites[].wpe.account_name keyed by
    site_key. Sites without a wpe block (CF-only) just skip the chip."""
    snap = {
        "alerts": [_alert("plasticsurgerynorth.com", "cache_hit_low",
                          severity="critical")],
        "sites": [
            {"key": "plasticsurgerynorth.com",
             "wpe": {"account_name": "nkpmedical4"}},
        ],
    }
    html = _needs_attention(snap)
    assert '<span class="alert-account">nkpmedical4</span>' in html
    # Chip appears BETWEEN the site link and the rule name so the read
    # order is site -> server -> rule -> summary.
    assert html.index("plasticsurgerynorth.com</a>") < html.index("nkpmedical4")
    assert html.index("nkpmedical4") < html.index("cache_hit_low")


def test_alert_row_omits_account_chip_for_cf_only_sites():
    """Sites without a wpe block (CF-only zones) must NOT render an empty
    chip — better to omit than to show 'unknown' which the operator would
    misread as a data quality problem."""
    snap = {
        "alerts": [_alert("cfonly.com", "edge_5xx_rate")],
        "sites": [{"key": "cfonly.com", "wpe": None}],
    }
    html = _needs_attention(snap)
    assert "alert-account" not in html
    assert "cfonly.com" in html


def test_alert_row_omits_account_chip_for_fleet_level_alert():
    """analytics_token_failure uses site_key='fleet' — no per-site mapping,
    no chip. Stays consistent with the link-skip behaviour for the same
    fleet-level alert class."""
    snap = {
        "alerts": [_alert("fleet", "analytics_token_failure",
                          severity="critical")],
        "sites": [],
    }
    html = _needs_attention(snap)
    assert "alert-account" not in html


def test_all_clear_path_unaffected():
    """When there are no NEW alerts, the all-clear state must still render
    cleanly — the link logic is inside the alerts loop and shouldn't leak."""
    snap = {"alerts": [_alert("x.com", "y", severity="warning")],
            "roster_summary": {"total": 200}, "date": "2026-06-23"}
    # Mark the only alert as resolved so shown list is empty.
    snap["alerts"][0]["state"] = "resolved"
    html = _needs_attention(snap)
    assert "All clear" in html
    assert "<a class=\"alert-site\"" not in html
