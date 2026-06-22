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
