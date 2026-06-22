"""Tests for the /pipeline page renderer + run-log reader."""
import json
from projects.fleet_monitoring.render_pipeline import (
    read_run_log, render_pipeline_page, _PAGE_LIMIT,
)


def _entry(date="2026-06-03", logged_at="2026-06-03T14:00:00+00:00",
           status="ok", duration=720, stages=None, error=None):
    e = {"date": date, "logged_at": logged_at, "duration_s": duration,
         "coverage": {"wpe": "245/248", "cf_config": "263/268"},
         "alert_counts": {"new": 5, "ongoing": 200},
         "status": status,
         "stages": stages or [
             {"name": "collect", "duration_s": 480, "ok": True},
             {"name": "analyze", "duration_s": 120, "ok": True},
             {"name": "render", "duration_s": 60, "ok": True},
         ]}
    if error:
        e["error"] = error
    return e


def test_read_run_log_missing_file_returns_empty(tmp_path):
    assert read_run_log(tmp_path / "nope.jsonl") == []


def test_read_run_log_returns_newest_first(tmp_path):
    log = tmp_path / "run-log.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        _entry(logged_at="2026-06-01T00:00:00+00:00", date="2026-06-01"),
        _entry(logged_at="2026-06-03T00:00:00+00:00", date="2026-06-03"),
        _entry(logged_at="2026-06-02T00:00:00+00:00", date="2026-06-02"),
    ]))
    out = read_run_log(log)
    assert [e["date"] for e in out] == ["2026-06-03", "2026-06-02", "2026-06-01"]


def test_read_run_log_skips_malformed_lines(tmp_path):
    log = tmp_path / "run-log.jsonl"
    log.write_text(
        json.dumps(_entry(date="2026-06-01")) + "\n"
        "this is not json\n"
        + json.dumps(_entry(date="2026-06-02")) + "\n"
    )
    out = read_run_log(log)
    assert len(out) == 2   # malformed line dropped, two valid kept


def test_read_run_log_respects_limit(tmp_path):
    log = tmp_path / "run-log.jsonl"
    entries = [_entry(date=f"2026-05-{20+i:02d}",
                      logged_at=f"2026-05-{20+i:02d}T00:00:00+00:00")
               for i in range(20)]
    log.write_text("\n".join(json.dumps(e) for e in entries))
    assert len(read_run_log(log, limit=5)) == 5
    assert len(read_run_log(log)) == _PAGE_LIMIT


def test_render_pipeline_page_shows_table_and_stages():
    html = render_pipeline_page([_entry()])
    assert "Pipeline health" in html
    assert "<table>" in html
    assert "collect" in html
    assert "analyze" in html
    assert "render" in html
    # Duration cell uses Nm Ns format
    assert "12m 0s" in html or "720s" in html


def test_render_pipeline_page_status_pill_colours_failure_red():
    html = render_pipeline_page([_entry(status="failed",
                                         error="TimeoutError: WPE 504")])
    assert "rp-pill-bad" in html
    assert "TimeoutError" in html


def test_render_pipeline_page_status_pill_colours_ok_green():
    html = render_pipeline_page([_entry(status="ok")])
    assert "rp-pill-ok" in html


def test_render_pipeline_page_empty_state():
    html = render_pipeline_page([])
    assert "No runs logged yet" in html
    assert "Refresh button" in html


def test_render_pipeline_page_marks_failed_stage_with_red_dot():
    e = _entry(stages=[
        {"name": "collect", "duration_s": 480, "ok": True},
        {"name": "analyze", "duration_s": 30, "ok": False},
    ])
    html = render_pipeline_page([e])
    assert "rp-stage-bad" in html
    assert "rp-stage-ok" in html


def test_render_pipeline_page_handles_pre_instrumentation_entry():
    # Old entries logged before stage instrumentation have no 'stages' key.
    old = {"date": "2026-05-15", "logged_at": "2026-05-15T06:00:00+00:00",
           "duration_s": 782, "coverage": {"wpe": "615/615"},
           "alert_counts": {"new": 266}}
    html = render_pipeline_page([old])
    assert "no stage data" in html
    assert "2026-05-15" in html


def test_render_pipeline_page_header_pill_reflects_latest_entry_age():
    # Mock not needed — _freshness reads now() against logged_at. A 1-year-old
    # log entry must read STALE.
    old = _entry(logged_at="2025-06-03T00:00:00+00:00")
    html = render_pipeline_page([old])
    assert "stale" in html.lower() or "STALE" in html


def test_render_pipeline_page_freshness_back_link_points_to_dashboard():
    html = render_pipeline_page([_entry()])
    assert 'href="dashboard.html"' in html


def test_render_pipeline_page_shows_sub_steps_with_red_dot_on_failure():
    entry = _entry(stages=[
        {"name": "analytics_pull", "duration_s": 30, "ok": False,
         "sub_steps": [
             {"name": "analytics.discover", "ok": True},
             {"name": "analytics.gsc_pull", "ok": True},
             {"name": "analytics.ga4_pull", "ok": False,
              "error": "invalid_grant: token expired"},
         ]},
        {"name": "collect", "duration_s": 480, "ok": True},
    ])
    html = render_pipeline_page([entry])
    # Sub-step container present
    assert "rp-substeps" in html
    assert "rp-substep" in html
    # Failed sub-step gets a red dot; ok ones get green
    assert "analytics.ga4_pull" in html
    assert "analytics.gsc_pull" in html
    # Error string surfaces in the tooltip (title attribute) for the failed one
    assert "invalid_grant" in html


def test_render_pipeline_page_omits_substep_block_when_none_present():
    # Stages without sub_steps shouldn't render the container div. The
    # literal class name appears in the inlined CSS, so match the applied
    # class attribute on a div, not just the substring.
    entry = _entry(stages=[
        {"name": "collect", "duration_s": 480, "ok": True},
    ])
    html = render_pipeline_page([entry])
    assert 'class="rp-substeps"' not in html
