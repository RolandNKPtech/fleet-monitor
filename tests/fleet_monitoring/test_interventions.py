from projects.fleet_monitoring.interventions import (
    load_interventions, intervention_fingerprint)


def test_load_interventions_empty_when_file_missing(tmp_path):
    assert load_interventions(tmp_path / "nope.yml") == []


def test_load_interventions_empty_when_only_header(tmp_path):
    f = tmp_path / "interventions.yml"
    f.write_text("interventions:\n", encoding="utf-8")
    assert load_interventions(f) == []


def test_load_interventions_returns_entries(tmp_path):
    f = tmp_path / "interventions.yml"
    f.write_text(
        "interventions:\n"
        "  - site: a.com\n"
        "    status: confirmed\n", encoding="utf-8")
    out = load_interventions(f)
    assert out == [{"site": "a.com", "status": "confirmed"}]


def test_load_interventions_raises_on_malformed(tmp_path):
    f = tmp_path / "interventions.yml"
    f.write_text("- just\n- a\n- list\n", encoding="utf-8")  # no interventions: key
    import pytest
    with pytest.raises(ValueError):
        load_interventions(f)


def test_intervention_fingerprint_is_stable():
    fp1 = intervention_fingerprint("a.com", "2026-05-13", "ssl", "strict", "full")
    fp2 = intervention_fingerprint("a.com", "2026-05-13", "ssl", "strict", "full")
    assert fp1 == fp2
    assert fp1.startswith("a.com:2026-05-13:")


def test_intervention_fingerprint_differs_on_change():
    fp1 = intervention_fingerprint("a.com", "2026-05-13", "ssl", "strict", "full")
    fp2 = intervention_fingerprint("a.com", "2026-05-13", "ssl", "full", "strict")
    assert fp1 != fp2


def test_models_exposes_intervention_paths():
    from projects.fleet_monitoring.models import INTERVENTIONS_FILE, FLEET_DB
    assert INTERVENTIONS_FILE.name == "interventions.yml"
    assert INTERVENTIONS_FILE.parent.name == "config"
    assert FLEET_DB.name == "fleet.db"


def test_seed_interventions_yml_parses_to_empty_list():
    import yaml
    from projects.fleet_monitoring.models import INTERVENTIONS_FILE
    data = yaml.safe_load(INTERVENTIONS_FILE.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "interventions" in data
    assert (data["interventions"] or []) == []


from projects.fleet_monitoring.interventions import _guess_type, detect_drafts


def test_guess_type_prefix_matches_real_drift_vocabulary():
    assert _guess_type("waf_rule_added") == "cf_waf_rule"
    assert _guess_type("waf_rule_changed") == "cf_waf_rule"
    assert _guess_type("cache_rule_removed") == "cf_cache_rule"
    assert _guess_type("ssl_downgrade") == "cf_ssl"
    assert _guess_type("tls_change") == "cf_ssl"
    assert _guess_type("bot_toggle_disabled") == "cf_bot"
    assert _guess_type("dns_proxy_lost") == "cf_proxy"
    assert _guess_type("something_unknown") == "cf_config_change"
    assert _guess_type("") == "cf_config_change"


def _drift_alert(site, kind, field, old, new, attribution):
    return {"site_key": site, "rule": "config_drift", "severity": "warning",
            "summary": "x", "detail": {"kind": kind, "field": field,
            "old": old, "new": new, "attribution": attribution}}


def test_detect_drafts_only_drafts_us_attributed_drift():
    snap = {"date": "2026-05-20", "alerts": [
        _drift_alert("a.com", "waf_rule_added", "waf", "0 rules", "1 rule", "us"),
        _drift_alert("b.com", "ssl_downgrade", "ssl", "strict", "full", "external"),
        {"site_key": "c.com", "rule": "bandwidth_spike", "severity": "warning",
         "summary": "x", "detail": {}},   # not config_drift
    ]}
    drafts = detect_drafts(snap, existing=[])
    assert len(drafts) == 1
    d = drafts[0]
    assert d["site"] == "a.com"
    assert d["applied_date"] == "2026-05-20"
    assert d["type"] == "cf_waf_rule"
    assert d["target_metric"] == "bandwidth"
    assert d["status"] == "needs_review"
    assert d["fingerprint"].startswith("a.com:2026-05-20:")


def test_detect_drafts_dedups_against_existing():
    snap = {"date": "2026-05-20", "alerts": [
        _drift_alert("a.com", "waf_rule_added", "waf", "0 rules", "1 rule", "us"),
    ]}
    first = detect_drafts(snap, existing=[])
    again = detect_drafts(snap, existing=first)
    assert again == []


def test_detect_drafts_dedups_within_one_snapshot():
    snap = {"date": "2026-05-20", "alerts": [
        _drift_alert("a.com", "waf_rule_added", "waf", "0 rules", "1 rule", "us"),
        _drift_alert("a.com", "waf_rule_added", "waf", "0 rules", "1 rule", "us"),
    ]}
    assert len(detect_drafts(snap, existing=[])) == 1


from projects.fleet_monitoring.interventions import append_drafts


def _sample_draft(site="a.com", fp="a.com:2026-05-20:abc12345"):
    return {"site": site, "applied_date": "2026-05-20", "type": "cf_waf_rule",
            "target_metric": "bandwidth",
            "description": "waf: 0 rules -> 1 rule (kind: waf_rule_added)",
            "status": "needs_review", "fingerprint": fp}


def test_append_drafts_creates_seed_file_when_missing(tmp_path):
    f = tmp_path / "interventions.yml"
    n = append_drafts(f, [_sample_draft()])
    assert n == 1
    out = load_interventions(f)
    assert len(out) == 1 and out[0]["site"] == "a.com"
    assert out[0]["fingerprint"] == "a.com:2026-05-20:abc12345"


def test_append_drafts_is_pure_append_preserving_prior_content(tmp_path):
    f = tmp_path / "interventions.yml"
    f.write_text(
        "# my comment\ninterventions:\n"
        "  - site: existing.com\n"
        "    status: confirmed\n", encoding="utf-8")
    append_drafts(f, [_sample_draft()])
    text = f.read_text(encoding="utf-8")
    assert "# my comment" in text          # comment preserved
    assert "existing.com" in text          # prior entry preserved
    out = load_interventions(f)
    assert len(out) == 2


def test_append_drafts_handles_missing_trailing_newline(tmp_path):
    f = tmp_path / "interventions.yml"
    f.write_text("interventions:", encoding="utf-8")  # NO trailing newline
    append_drafts(f, [_sample_draft()])
    out = load_interventions(f)
    assert len(out) == 1


def test_append_drafts_description_with_colons_round_trips(tmp_path):
    f = tmp_path / "interventions.yml"
    d = _sample_draft()
    d["description"] = "ssl: strict -> full (kind: ssl_downgrade)"
    append_drafts(f, [d])
    out = load_interventions(f)
    assert out[0]["description"] == "ssl: strict -> full (kind: ssl_downgrade)"


def test_append_drafts_noop_on_empty(tmp_path):
    f = tmp_path / "interventions.yml"
    assert append_drafts(f, []) == 0
    assert not f.exists()
