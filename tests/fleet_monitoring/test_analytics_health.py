"""Tests for fleet-level analytics_token_failure evaluator + sub-step capture."""
import json
from projects.fleet_monitoring.analytics_health import (
    evaluate_analytics_health, _latest_entry, FLEET_SITE_KEY,
)


def _entry(stages, date="2026-06-03", logged_at="2026-06-03T22:00:00+00:00",
           status="ok"):
    return {"date": date, "logged_at": logged_at, "duration_s": 700,
            "coverage": {}, "alert_counts": {}, "status": status,
            "stages": stages}


def _write_log(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n",
                    encoding="utf-8")


def test_no_alerts_when_run_log_missing(tmp_path):
    assert evaluate_analytics_health(path=tmp_path / "no.jsonl") == []


# ---------- current_stages path (chicken-and-egg fix) ----------------------

def test_current_stages_param_fires_without_writing_run_log():
    """The IN-FLIGHT stages from run.py are passed directly — the rule must
    detect failures THIS run, not just yesterday's on-disk run-log entry.
    Chicken-and-egg fix: the run-log entry for THIS run isn't written
    until the finally: block, AFTER analyze runs."""
    stages = [
        {"name": "r2_pull", "ok": True, "duration_s": 2.0},
        {"name": "analytics_pull", "ok": False, "duration_s": 700,
         "sub_steps": [
             {"name": "analytics.discover", "ok": True},
             {"name": "analytics.gsc_pull", "ok": True},
             {"name": "analytics.ga4_pull", "ok": False,
              "error": "timeout after 1800s"},
         ]},
    ]
    alerts = evaluate_analytics_health(current_stages=stages)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "critical"
    assert a.rule == "analytics_token_failure"
    assert a.detail["short_source"] == "ga4_pull"
    assert "timeout" in a.detail["error"]


def test_current_stages_returns_empty_when_all_ok():
    stages = [
        {"name": "analytics_pull", "ok": True,
         "sub_steps": [
             {"name": "analytics.discover", "ok": True},
             {"name": "analytics.gsc_pull", "ok": True},
             {"name": "analytics.ga4_pull", "ok": True},
         ]},
    ]
    assert evaluate_analytics_health(current_stages=stages) == []


def test_current_stages_takes_precedence_over_disk_log(tmp_path):
    """current_stages wins. The whole point is to surface THIS run's
    failures, not yesterday's on-disk ones."""
    log = tmp_path / "run-log.jsonl"
    _write_log(log, [_entry(stages=[
        {"name": "analytics_pull", "ok": False,
         "sub_steps": [
             {"name": "analytics.gsc_pull", "ok": False, "error": "old"},
             {"name": "analytics.ga4_pull", "ok": True},
         ]},
    ])])
    in_flight = [
        {"name": "analytics_pull", "ok": False,
         "sub_steps": [
             {"name": "analytics.gsc_pull", "ok": True},
             {"name": "analytics.ga4_pull", "ok": False, "error": "new"},
         ]},
    ]
    alerts = evaluate_analytics_health(current_stages=in_flight, path=log)
    assert len(alerts) == 1
    assert alerts[0].detail["short_source"] == "ga4_pull"
    assert alerts[0].detail["error"] == "new"


def test_current_stages_empty_list_returns_no_alerts():
    # Distinct from None — empty list means "no stages with sub_steps".
    assert evaluate_analytics_health(current_stages=[]) == []


def test_no_alerts_when_latest_run_all_ok(tmp_path):
    log = tmp_path / "run-log.jsonl"
    _write_log(log, [_entry(stages=[
        {"name": "analytics_pull", "duration_s": 30, "ok": True,
         "sub_steps": [
             {"name": "analytics.discover", "ok": True},
             {"name": "analytics.gsc_pull", "ok": True},
             {"name": "analytics.ga4_pull", "ok": True},
         ]},
        {"name": "collect", "duration_s": 600, "ok": True},
    ])])
    assert evaluate_analytics_health(path=log) == []


def test_fires_critical_when_ga4_pull_failed(tmp_path):
    log = tmp_path / "run-log.jsonl"
    _write_log(log, [_entry(stages=[
        {"name": "analytics_pull", "duration_s": 30, "ok": False,
         "sub_steps": [
             {"name": "analytics.discover", "ok": True},
             {"name": "analytics.gsc_pull", "ok": True},
             {"name": "analytics.ga4_pull", "ok": False,
              "error": "invalid_grant: Token has been expired or revoked."},
         ]},
    ])])
    alerts = evaluate_analytics_health(path=log)
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == "critical"
    assert a.rule == "analytics_token_failure"
    assert a.site_key == FLEET_SITE_KEY
    assert "ga4_pull" in a.summary
    assert "Re-mint" in a.summary
    assert a.detail["short_source"] == "ga4_pull"
    assert "invalid_grant" in a.detail["error"]


def test_fires_one_alert_per_failed_source(tmp_path):
    log = tmp_path / "run-log.jsonl"
    _write_log(log, [_entry(stages=[
        {"name": "analytics_pull", "duration_s": 30, "ok": False,
         "sub_steps": [
             {"name": "analytics.discover", "ok": True},
             {"name": "analytics.gsc_pull", "ok": False, "error": "401"},
             {"name": "analytics.ga4_pull", "ok": False, "error": "403"},
         ]},
    ])])
    alerts = evaluate_analytics_health(path=log)
    assert len(alerts) == 2
    sources = {a.detail["short_source"] for a in alerts}
    assert sources == {"gsc_pull", "ga4_pull"}


def test_dedup_key_per_source_keeps_alerts_distinct(tmp_path):
    log = tmp_path / "run-log.jsonl"
    _write_log(log, [_entry(stages=[
        {"name": "analytics_pull", "ok": False,
         "sub_steps": [
             {"name": "analytics.gsc_pull", "ok": False, "error": "x"},
             {"name": "analytics.ga4_pull", "ok": False, "error": "y"},
         ]},
    ])])
    fps = {a.fingerprint() for a in evaluate_analytics_health(path=log)}
    # Two distinct fingerprints — one per failing source.
    assert fps == {
        "fleet:analytics_token_failure:analytics.gsc_pull",
        "fleet:analytics_token_failure:analytics.ga4_pull",
    }


def test_reads_latest_entry_not_oldest(tmp_path):
    log = tmp_path / "run-log.jsonl"
    _write_log(log, [
        _entry(stages=[{"name": "analytics_pull", "ok": False, "sub_steps": [
            {"name": "analytics.ga4_pull", "ok": False, "error": "old"}]}],
            logged_at="2026-06-01T00:00:00+00:00"),
        _entry(stages=[{"name": "analytics_pull", "ok": True, "sub_steps": [
            {"name": "analytics.ga4_pull", "ok": True}]}],
            logged_at="2026-06-03T00:00:00+00:00"),
    ])
    # Latest run was clean — no alert, even though earlier failed.
    assert evaluate_analytics_health(path=log) == []


def test_ignores_stages_without_sub_steps(tmp_path):
    # `collect`, `analyze`, `render` etc. don't carry sub_steps.
    log = tmp_path / "run-log.jsonl"
    _write_log(log, [_entry(stages=[
        {"name": "collect", "duration_s": 600, "ok": False},
        {"name": "render", "duration_s": 5, "ok": False},
    ])])
    # Stage failures matter, but they're not analytics token failures.
    assert evaluate_analytics_health(path=log) == []


def test_tolerates_malformed_log(tmp_path):
    log = tmp_path / "run-log.jsonl"
    log.write_text("not json\n" + json.dumps(_entry(stages=[
        {"name": "analytics_pull", "ok": False, "sub_steps": [
            {"name": "analytics.ga4_pull", "ok": False, "error": "fail"}]}])
    ) + "\n", encoding="utf-8")
    assert len(evaluate_analytics_health(path=log)) == 1


def test_latest_entry_returns_none_for_missing_file(tmp_path):
    assert _latest_entry(tmp_path / "no.jsonl") is None


def test_latest_entry_skips_blank_lines(tmp_path):
    log = tmp_path / "run-log.jsonl"
    log.write_text("\n\n" + json.dumps(_entry(stages=[])) + "\n\n",
                   encoding="utf-8")
    assert _latest_entry(log) is not None
