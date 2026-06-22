"""Render the /pipeline health page from data/run-log.jsonl entries.

The page is the operator's "is the pipeline actually working?" view. It
deliberately answers questions other surfaces can't: did last night's
cron run? how long has each stage been taking? did something fail?

Generated on-demand by serve.py — NOT a static file — so the most recent
in-progress run always shows up the moment its run-log entry is appended.
"""
from __future__ import annotations
import html as _html
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import RUN_LOG_FILE, freshness as _freshness


_PAGE_LIMIT = 14    # last two weeks of daily runs is enough context


def _esc(v) -> str:
    return _html.escape(str(v))


def read_run_log(path: Path = RUN_LOG_FILE, limit: int = _PAGE_LIMIT) -> list[dict]:
    """Return up to `limit` most-recent run-log entries, newest first.

    Malformed lines are skipped silently — a partially-corrupt log shouldn't
    blank the page; a missing log shows the empty-state."""
    if not path.exists():
        return []
    entries: list[dict] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    entries.sort(key=lambda e: e.get("logged_at", ""), reverse=True)
    return entries[:limit]


def _status_pill(entry: dict) -> str:
    status = entry.get("status") or "ok"
    cls = {"ok": "rp-pill-ok", "failed": "rp-pill-bad"}.get(status, "rp-pill-warn")
    return f'<span class="rp-pill {cls}">{_esc(status)}</span>'


def _fmt_duration(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "—"
    if s < 60:
        return f"{s}s"
    return f"{s // 60}m {s % 60}s"


def _stages_cell(entry: dict) -> str:
    """Compact per-stage timing list. Failed stages get a red dot. Stages
    with sub_steps (today: only analytics_pull) expand inline with one
    dot per sub-step so a failed GA4 / GSC pull is immediately visible
    even when the wrapping stage's subprocess returned 0."""
    stages = entry.get("stages") or []
    if not stages:
        return '<span class="rp-muted">no stage data — pre-instrumentation run</span>'
    parts = []
    for st in stages:
        dot = "rp-stage-bad" if not st.get("ok", True) else "rp-stage-ok"
        sub_html = ""
        sub_steps = st.get("sub_steps") or []
        if sub_steps:
            sub_parts = []
            for ss in sub_steps:
                sdot = "rp-stage-bad" if not ss.get("ok", True) else "rp-stage-ok"
                tip = ss.get("error") or "ok"
                sub_parts.append(
                    f'<span class="rp-substep" title="{_esc(tip)}">'
                    f'<span class="rp-stage-dot {sdot}"></span>'
                    f'{_esc(ss.get("name","?"))}</span>')
            sub_html = (f'<div class="rp-substeps">{"".join(sub_parts)}</div>')
        parts.append(
            f'<span class="rp-stage"><span class="rp-stage-dot {dot}"></span>'
            f'{_esc(st.get("name","?"))} '
            f'<span class="rp-muted">{_fmt_duration(st.get("duration_s"))}</span>'
            f'{sub_html}</span>')
    return ''.join(parts)


def _coverage_cell(cov: dict) -> str:
    if not cov:
        return '<span class="rp-muted">—</span>'
    bits = []
    for k in ("wpe", "cf_config"):
        v = cov.get(k)
        if v:
            bits.append(f'{_esc(k)}: <b>{_esc(v)}</b>')
    return ' &middot; '.join(bits) or '<span class="rp-muted">—</span>'


def _alerts_cell(counts: dict) -> str:
    if not counts:
        return '<span class="rp-muted">—</span>'
    bits = []
    for k in ("new", "ongoing", "resolved", "muted"):
        n = counts.get(k, 0)
        if n:
            bits.append(f'{_esc(k)}: <b>{n}</b>')
    return ' &middot; '.join(bits) or '<span class="rp-muted">0</span>'


def _row(entry: dict) -> str:
    ts = entry.get("logged_at", "?")
    error_row = ""
    if entry.get("error"):
        error_row = (f'<tr class="rp-err-row"><td colspan="6">'
                     f'<span class="rp-muted">error:</span> '
                     f'<code>{_esc(entry["error"])}</code></td></tr>')
    return (
        '<tr>'
        f'<td>{_esc(entry.get("date","?"))}<div class="rp-muted">{_esc(ts)}</div></td>'
        f'<td>{_status_pill(entry)}</td>'
        f'<td>{_fmt_duration(entry.get("duration_s"))}</td>'
        f'<td>{_stages_cell(entry)}</td>'
        f'<td>{_coverage_cell(entry.get("coverage") or {})}</td>'
        f'<td>{_alerts_cell(entry.get("alert_counts") or {})}</td>'
        '</tr>'
        + error_row
    )


_RP_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:24px;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",
  Roboto,sans-serif;background:#fafbfc;color:#14151a;font-size:13px}
.rp-head{display:flex;align-items:baseline;justify-content:space-between;
  margin-bottom:18px}
.rp-head h1{margin:0;font-size:20px;font-weight:700}
.rp-head a{color:#2563eb;text-decoration:none;font-size:12px}
.rp-head a:hover{text-decoration:underline}
.rp-fresh{font-size:11.5px;padding:3px 10px;border-radius:999px;font-weight:600}
.rp-fresh.fresh{background:#dcfce7;color:#15803d}
.rp-fresh.aging{background:#fef3c7;color:#b45309}
.rp-fresh.stale{background:#fee2e2;color:#b91c1c}
table{width:100%;border-collapse:collapse;background:#fff;
  border:1px solid #ececef;border-radius:12px;overflow:hidden}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #f1f2f4;
  vertical-align:top;font-variant-numeric:tabular-nums}
th{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  color:#6a6f78;background:#fafbfc}
tr:last-child td{border-bottom:0}
.rp-pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:10.5px;
  font-weight:700;text-transform:uppercase;letter-spacing:.04em}
.rp-pill-ok{background:#dcfce7;color:#15803d}
.rp-pill-bad{background:#fee2e2;color:#b91c1c}
.rp-pill-warn{background:#fef3c7;color:#b45309}
.rp-muted{color:#9aa0aa;font-size:11.5px}
.rp-stage{display:inline-flex;align-items:center;gap:4px;
  margin-right:10px;font-size:12px;white-space:nowrap;flex-wrap:wrap}
.rp-stage-dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.rp-stage-ok{background:#22c55e}
.rp-stage-bad{background:#dc2626}
.rp-substeps{display:flex;gap:8px;margin-left:14px;font-size:11px;
  color:#6a6f78;width:100%;margin-top:2px;flex-wrap:wrap}
.rp-substep{display:inline-flex;align-items:center;gap:3px;cursor:help}
.rp-err-row td{background:#fef2f2;padding:6px 12px;font-size:11.5px}
.rp-err-row code{font-family:ui-monospace,"SF Mono",Consolas,monospace;
  background:#fee2e2;padding:1px 5px;border-radius:3px}
.rp-empty{padding:40px;text-align:center;color:#6a6f78;background:#fff;
  border:1px solid #ececef;border-radius:12px}
"""


def render_pipeline_page(entries: list[dict]) -> str:
    """Full HTML for the /pipeline health view."""
    if not entries:
        body = ('<div class="rp-empty">No runs logged yet. '
                'Trigger a run from the dashboard Refresh button.</div>')
        head_pill = ''
    else:
        latest_ts = entries[0].get("logged_at", "")
        fresh_label, fresh_class = _freshness(latest_ts)
        head_pill = (f'<span class="rp-fresh {fresh_class}">'
                     f'{_esc(fresh_label)}</span>')
        rows = "".join(_row(e) for e in entries)
        body = (
            '<table>'
            '<thead><tr>'
            '<th>Date / logged at</th>'
            '<th>Status</th>'
            '<th>Duration</th>'
            '<th>Stages</th>'
            '<th>Coverage</th>'
            '<th>Alerts</th>'
            '</tr></thead>'
            f'<tbody>{rows}</tbody>'
            '</table>')

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pipeline health &middot; Fleet Monitor</title>
<style>{_RP_CSS}</style></head><body>
<div class="rp-head">
  <h1>Pipeline health</h1>
  <div>{head_pill}
    &nbsp;<a href="dashboard.html">&larr; Back to dashboard</a></div>
</div>
{body}
<p class="rp-muted" style="margin-top:14px">
  Showing up to {_PAGE_LIMIT} most recent runs from
  <code>data/run-log.jsonl</code>. Each Refresh appends one entry.
  Stage timing is captured per-run; older runs without timing predate
  the instrumentation.
</p>
</body></html>"""
