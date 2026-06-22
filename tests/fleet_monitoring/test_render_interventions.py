from projects.fleet_monitoring.render import _interventions_tab


def _row(site="a.com", supported=True, v7="worked", d7=-34.0):
    return {"site": site, "applied_date": "2026-05-13", "type": "cf_waf_rule",
            "target_metric": "bandwidth", "supported": supported,
            "horizons": {7: {"verdict": v7, "delta_pct": d7},
                         30: {"verdict": "too_early", "delta_pct": None},
                         90: {"verdict": "too_early", "delta_pct": None}}}


def test_interventions_tab_empty_state():
    html = _interventions_tab({"needs_review": 0, "rows": []})
    assert "Interventions" in html
    assert "config/interventions.yml" in html


def test_interventions_tab_needs_review_banner():
    html = _interventions_tab({"needs_review": 3, "rows": []})
    assert "3" in html
    assert "review" in html.lower()


def test_interventions_tab_renders_intervention_row_with_verdicts():
    html = _interventions_tab({"needs_review": 0, "rows": [_row()]})
    assert "a.com" in html
    assert "cf_waf_rule" in html
    assert "worked" in html
    assert "-34" in html or "−34" in html
    assert "too_early" in html or "too early" in html.lower()


def test_interventions_tab_unsupported_metric_row():
    html = _interventions_tab({"needs_review": 0,
                               "rows": [_row(supported=False)]})
    assert "not supported" in html.lower()


def test_interventions_tab_aggregate_panel_groups_by_type():
    rows = [_row(site="a.com"), _row(site="b.com")]
    html = _interventions_tab({"needs_review": 0, "rows": rows})
    assert "cf_waf_rule" in html
    assert "2" in html
