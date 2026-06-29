# projects/fleet_monitoring/render.py
"""Render stage — latest snapshot + alerts -> a single self-contained dashboard.html.

Four tabs (Overview, Sites, Trends, Changelog). Plain string formatting, an inline
<style> design system, and one inline <script> for tab switching, table sort,
site filter, and the Refresh button.  No template engine, no external assets,
no font CDNs — the file opens anywhere.

Visual direction: light off-white background, white cards with a soft shadow,
a single LIME-green active pill in the top-center pill nav, trend arrows on
KPI cards, a bar chart with a "selected bar" callout bubble, a smooth SVG
line chart for the alerts trend, and per-account bandwidth bars with inline
Δ-vs-yesterday deltas.  Deliberately uses the Segoe UI Variable / SF Pro
display + tabular-nums system stack so numbers align without a web font.
"""
from __future__ import annotations
import argparse
import html as html_mod
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone

from datetime import date as _date

from .cycle import cycle_window, cycle_to_date_gb, cycle_to_date_visits
from .models import SNAPSHOTS_DIR, DASHBOARD_FILE, CONSOLE_FILE, SEVERITY_ORDER
from .plan_config import AccountPlan, account_is_configured, load_plans, primary_lookup_name
from .plan_utilization import count_data_days, MIN_PROJECTION_DAYS
from .timeseries import read_all, read_daily_all
from .render_console import render_console
from .render_site import safe_key as _safe_key, write_all_site_pages



def _esc(v) -> str:
    return html_mod.escape(str(v))


from .models import freshness as _freshness  # noqa: F401 (re-export for back-compat)


# ---------------------------------------------------------------------------
# Aggregation helpers — pure functions used by multiple panels
# ---------------------------------------------------------------------------


def _account_bandwidth(snapshot: dict) -> dict[str, float]:
    """Sum each site's 30-day rolling bandwidth grouped by friendly account name."""
    acct: dict[str, float] = {}
    for s in snapshot.get("sites", []):
        wpe = s.get("wpe") or {}
        a = wpe.get("account_name") or wpe.get("account")
        if a and wpe.get("bandwidth_gb_30d") is not None:
            acct[a] = acct.get(a, 0) + wpe["bandwidth_gb_30d"]
    return acct


def _account_totals_by_date(timeseries_rows: list[dict]) -> dict[str, dict[str, float]]:
    """{date: {account_name: total_gb}} from timeseries rows.

    Rows without an `account` field are skipped (older rollups before the
    account column was added).
    """
    out: dict[str, dict[str, float]] = {}
    for r in timeseries_rows:
        if r.get("bandwidth_gb") is None:
            continue
        a = r.get("account")
        if not a:
            continue
        d = r["date"]
        bucket = out.setdefault(d, {})
        bucket[a] = bucket.get(a, 0) + r["bandwidth_gb"]
    return out


def _fleet_total_by_date(timeseries_rows: list[dict]) -> dict[str, float]:
    """Sum every site's bandwidth into a fleet total per snapshot date."""
    by_date: dict[str, float] = {}
    for r in timeseries_rows:
        if r.get("bandwidth_gb") is not None:
            by_date[r["date"]] = by_date.get(r["date"], 0) + r["bandwidth_gb"]
    return by_date


def _per_account_by_date(timeseries_rows: list[dict]) -> dict[str, dict[str, float]]:
    """Group bandwidth by account, then by date. Sum across sites.

    Rows with no account anchor surface under '(unassigned)' so the operator
    sees the gap rather than silently inflating one account.
    """
    out: dict[str, dict[str, float]] = {}
    for r in timeseries_rows:
        bw = r.get("bandwidth_gb")
        if bw is None:
            continue
        account = r.get("account") or "(unassigned)"
        out.setdefault(account, {})
        out[account][r["date"]] = out[account].get(r["date"], 0) + bw
    return out


# Stable color assignment by sorted account name so the same account keeps
# the same color across snapshots and rebuilds.  Six picks tuned for contrast
# on a white card AND for distinguishability for the most common color-vision
# deficiencies (deuteranopia / protanopia / tritanopia).
_ACCOUNT_PALETTE = [
    "#2563eb",  # blue
    "#dc2626",  # red
    "#16a34a",  # green
    "#d97706",  # amber
    "#7c3aed",  # purple
    "#0891b2",  # cyan
    "#db2777",  # pink (overflow)
    "#65a30d",  # lime  (overflow)
]


def _account_color(idx: int) -> str:
    return _ACCOUNT_PALETTE[idx % len(_ACCOUNT_PALETTE)]


def _per_account_lines_chart(per_account: dict[str, dict[str, float]]) -> str:
    """Multi-line SVG chart, one path per WPE account.

    Empty state when there is no data or only a single snapshot (one point
    can't form a line) — explains what's missing rather than rendering a
    misleading flat line at zero.
    """
    if not per_account:
        return ('<section class="panel"><div class="panel-head">'
                '<h2>Per-account bandwidth over time</h2></div>'
                '<p class="muted">No history yet &mdash; the daily Docker cron '
                'will populate this overnight.</p></section>')
    # Union of dates across all accounts, oldest first.
    all_dates = sorted({d for acct in per_account.values() for d in acct.keys()})
    if len(all_dates) < 2:
        return ('<section class="panel"><div class="panel-head">'
                '<h2>Per-account bandwidth over time</h2></div>'
                '<p class="muted">Need at least 2 snapshots to draw a line. '
                'Currently have ' + str(len(all_dates)) + '. Comes back tomorrow.'
                '</p></section>')

    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 720, 280, 48, 90, 18, 32
    n = len(all_dates)
    # Auto-scaled shared Y axis: fit the data band (min..max) with headroom
    # rather than anchoring at zero, so the lines use the chart's full height.
    all_vals = [v for acct in per_account.values() for v in acct.values()]
    vmin, vmax = min(all_vals), max(all_vals)
    span = vmax - vmin
    head = span * 0.08 if span > 0 else (vmax * 0.04 or 1.0)
    lo = max(vmin - head, 0.0)
    hi = vmax + head
    rng = hi - lo

    def x(i): return PAD_L + (W - PAD_L - PAD_R) * (i / max(n - 1, 1))
    def y(v): return PAD_T + (H - PAD_T - PAD_B) * (1 - (v - lo) / rng)

    sorted_accounts = sorted(per_account.keys())

    # Y-axis gridlines: 4 evenly spaced lines so the eye has reference points.
    grid_lines = []
    grid_labels = []
    for frac in (0, 0.25, 0.5, 0.75, 1):
        gy = PAD_T + (H - PAD_T - PAD_B) * (1 - frac)
        grid_lines.append(
            f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{W-PAD_R}" y2="{gy:.1f}" '
            f'class="pa-grid"/>')
        grid_labels.append(
            f'<text x="{PAD_L-6}" y="{gy+4:.1f}" class="pa-y" text-anchor="end">'
            f'{lo + rng*frac:,.0f}</text>')

    # X-axis date labels — first, last, and ~5 evenly spaced
    x_labels = []
    step = max(1, n // 5)
    for i in range(0, n, step):
        x_labels.append(
            f'<text x="{x(i):.1f}" y="{H-10}" class="pa-x" text-anchor="middle">'
            f'{_esc(all_dates[i][5:])}</text>')
    if (n - 1) % step != 0:
        x_labels.append(
            f'<text x="{x(n-1):.1f}" y="{H-10}" class="pa-x" text-anchor="middle">'
            f'{_esc(all_dates[-1][5:])}</text>')

    paths = []
    end_labels = []
    legend_items = []
    for idx, account in enumerate(sorted_accounts):
        color = _account_color(idx)
        series = per_account[account]
        # Project the series onto the shared date axis.  Missing dates skip
        # the point (line gaps signal "no data" rather than zero).
        pts = []
        for i, d in enumerate(all_dates):
            if d in series:
                pts.append((x(i), y(series[d]), d, series[d]))
        if not pts:
            continue
        # Build path with M ... L ... ; SVG line segments only connect
        # consecutive points (use M to lift the pen at gaps).
        d_str = []
        prev_date_i = None
        for px, py, d, _ in pts:
            this_i = all_dates.index(d)
            if prev_date_i is None or this_i != prev_date_i + 1:
                d_str.append(f"M {px:.1f} {py:.1f}")
            else:
                d_str.append(f"L {px:.1f} {py:.1f}")
            prev_date_i = this_i
        paths.append(
            f'<path d="{" ".join(d_str)}" class="pa-line" stroke="{color}" '
            f'fill="none" stroke-width="2.2" stroke-linecap="round" '
            f'stroke-linejoin="round"/>')
        # Endpoint dots with title= tooltip (date, GB)
        for px, py, d, v in pts:
            paths.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.5" fill="{color}">'
                f'<title>{_esc(account)} &middot; {_esc(d)}: {v:,.0f} GB</title>'
                f'</circle>')
        # Inline label at the right edge, next to the last point
        last_px, last_py, _, last_v = pts[-1]
        end_labels.append(
            f'<text x="{last_px+6:.1f}" y="{last_py+4:.1f}" class="pa-end" '
            f'fill="{color}">{_esc(account)}</text>')
        legend_items.append(
            f'<span class="pa-legend-item">'
            f'<span class="pa-swatch" style="background:{color}"></span>'
            f'{_esc(account)} '
            f'<span class="muted">&middot; {last_v:,.0f} GB</span></span>')

    svg = f'''
    <svg class="pa-chart" viewBox="0 0 {W} {H}"
         aria-label="Per-account bandwidth over time">
      {"".join(grid_lines)}
      {"".join(grid_labels)}
      {"".join(x_labels)}
      {"".join(paths)}
      {"".join(end_labels)}
    </svg>'''
    legend = (
        f'<div class="pa-legend">{"".join(legend_items)}</div>')
    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>Per-account bandwidth over time</h2>
        <span class="muted">{len(sorted_accounts)} WPE accounts &middot; daily totals in GB</span>
      </div>
      {svg}
      {legend}
      <p class="muted">Sum of all sites under each WPE account, one line per
        account.  Hover a point for the exact value &middot; gaps mean no data
        for that account on that day.</p>
    </section>"""


def _alerts_per_date(timeseries_rows: list[dict]) -> dict[str, int]:
    """Sum alert_count across sites per snapshot date."""
    by_date: dict[str, int] = {}
    for r in timeseries_rows:
        c = r.get("alert_count") or 0
        if c:
            by_date[r["date"]] = by_date.get(r["date"], 0) + c
        else:
            by_date.setdefault(r["date"], 0)
    return by_date


def _visits_per_date(timeseries_rows: list[dict]) -> dict[str, int]:
    """Sum billable_visits across sites per snapshot date."""
    by_date: dict[str, int] = {}
    for r in timeseries_rows:
        v = r.get("billable_visits") or 0
        by_date[r["date"]] = by_date.get(r["date"], 0) + int(v)
    return by_date


def _sites_per_date(timeseries_rows: list[dict]) -> dict[str, int]:
    """Count distinct sites per snapshot date."""
    seen: dict[str, set] = {}
    for r in timeseries_rows:
        seen.setdefault(r["date"], set()).add(r["key"])
    return {d: len(keys) for d, keys in seen.items()}


def _period_delta(by_date: dict[str, float], period_days: int) -> dict | None:
    """Compare today's value to the snapshot ~`period_days` ago.

    Returns {period_days, current_date, prev_date, current_gb, prev_gb,
    delta_gb, delta_pct} or None when no qualifying earlier snapshot exists.
    Field names use *_gb for backward compatibility — the helper is generic.
    """
    if not by_date:
        return None
    dates = sorted(by_date.keys())
    current_date = dates[-1]
    cur = date.fromisoformat(current_date)
    target = (cur - timedelta(days=period_days)).isoformat()
    prev_candidates = [d for d in dates if d <= target]
    if not prev_candidates:
        return None
    prev_date = prev_candidates[-1]
    current_gb = by_date[current_date]
    prev_gb = by_date[prev_date]
    delta_gb = current_gb - prev_gb
    delta_pct = (delta_gb / prev_gb * 100) if prev_gb else 0.0
    return {
        "period_days": period_days, "current_date": current_date,
        "prev_date": prev_date, "current_gb": current_gb, "prev_gb": prev_gb,
        "delta_gb": delta_gb, "delta_pct": delta_pct,
    }


# ---------------------------------------------------------------------------
# KPI helpers
# ---------------------------------------------------------------------------


def _prev_value(by_date: dict, today: str | None) -> tuple[str | None, float | int | None]:
    """Return (prev_date, prev_value) — the most recent date strictly before `today`."""
    if not by_date:
        return None, None
    earlier = sorted(d for d in by_date if not today or d < today)
    if not earlier:
        return None, None
    p = earlier[-1]
    return p, by_date[p]


def _trend_class(delta: float, lower_is_better: bool) -> tuple[str, str]:
    """Return (arrow_html, css_class). lower_is_better flips green/red sense."""
    if abs(delta) < 1e-6:
        return "&middot;", "trend-flat"
    going_down = delta < 0
    good = going_down if lower_is_better else not going_down
    cls = "trend-good" if good else "trend-bad"
    arrow = "&darr;" if going_down else "&uarr;"
    return arrow, cls


def _format_pct(pct: float) -> str:
    return f"{pct:+.1f}%"


def _kpi_deltas(snapshot: dict, timeseries_rows: list[dict]) -> dict:
    """Pre-compute every KPI card's current value, prior value, and Δ.

    Returns a dict keyed by metric name with sub-dicts containing the formatted
    pieces the KPI card renderer needs.
    """
    today = snapshot.get("date")

    bw_by = _fleet_total_by_date(timeseries_rows)
    sites_by = _sites_per_date(timeseries_rows)
    visits_by = _visits_per_date(timeseries_rows)
    alerts_by = _alerts_per_date(timeseries_rows)

    bw_now = bw_by.get(today) or sum(_account_bandwidth(snapshot).values())
    _, bw_prev = _prev_value(bw_by, today)
    sites_now = snapshot.get("roster_summary", {}).get("total") or sites_by.get(today, 0)
    _, sites_prev = _prev_value(sites_by, today)
    visits_now = visits_by.get(today, 0) or sum(
        (s.get("wpe") or {}).get("billable_visits_30d") or 0
        for s in snapshot.get("sites", []))
    _, visits_prev = _prev_value(visits_by, today)

    alerts = snapshot.get("alerts", [])
    new_alerts = sum(1 for a in alerts if a.get("state") == "new")
    _, alerts_prev = _prev_value(alerts_by, today)

    def _pkg(now, prev, *, lower_is_better, fmt):
        if prev is None or prev == 0:
            return {"now": now, "now_fmt": fmt(now), "delta_fmt": "first snapshot",
                    "cls": "trend-flat", "arrow": "&middot;"}
        delta = now - prev
        pct = (delta / prev * 100) if prev else 0
        arrow, cls = _trend_class(delta, lower_is_better)
        return {"now": now, "now_fmt": fmt(now),
                "delta_fmt": f"{arrow} {abs(delta):,.0f} ({_format_pct(pct)})",
                "cls": cls, "arrow": arrow, "prev": prev, "pct": pct}

    return {
        "bandwidth": _pkg(bw_now, bw_prev, lower_is_better=True,
                          fmt=lambda v: f"{v:,.0f}"),
        "sites": _pkg(sites_now, sites_prev, lower_is_better=False,
                      fmt=lambda v: f"{int(v):,}"),
        "visits": _pkg(visits_now, visits_prev, lower_is_better=False,
                       fmt=lambda v: f"{int(v):,}"),
        "alerts": _pkg(new_alerts, alerts_prev, lower_is_better=True,
                       fmt=lambda v: f"{int(v):,}"),
    }


def _stat_cards(snapshot: dict, timeseries_rows: list[dict] | None = None) -> str:
    """Top-of-Overview KPI row. Four cards in distinctive style.

    Class names retained for backwards-compat: `.stat-grid` / `.stat-card` /
    `.stat-label` / `.stat-value` / `.stat-sub` are still on the elements so
    existing tests and any external selectors continue to work.
    """
    d = _kpi_deltas(snapshot, timeseries_rows or [])

    def card(label, value, unit, sub_html, *, accent="", icon=""):
        accent_cls = f" stat-card-{accent}" if accent else ""
        return (
            f'<article class="stat-card{accent_cls}">'
            f'<div class="stat-card-head">'
            f'<span class="stat-label">{_esc(label)}</span>'
            f'<span class="stat-icon">{icon}</span>'
            f'</div>'
            f'<div class="stat-value-row">'
            f'<span class="stat-value">{value}</span>'
            f'<span class="stat-unit">{unit}</span>'
            f'</div>'
            f'<div class="stat-sub">{sub_html}</div>'
            f'</article>')

    bw = d["bandwidth"]
    sites = d["sites"]
    vis = d["visits"]
    al = d["alerts"]

    return f'''<section class="stat-grid">
{card("Total fleet bandwidth", bw["now_fmt"], "GB",
      f'<span class="trend {bw["cls"]}">{bw["delta_fmt"]}</span> vs last snapshot',
      icon='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l5-5 4 4 8-9"/><path d="M14 7h6v6"/></svg>')}
{card("Sites monitored", sites["now_fmt"], "",
      f'<span class="trend {sites["cls"]}">{sites["delta_fmt"]}</span> vs last snapshot',
      icon='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18"/></svg>')}
{card("Total visits (30d)", vis["now_fmt"], "",
      f'<span class="trend {vis["cls"]}">{vis["delta_fmt"]}</span> vs last snapshot',
      icon='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>')}
{card("New alerts", al["now_fmt"], "",
      f'<span class="trend {al["cls"]}">{al["delta_fmt"]}</span> vs last snapshot',
      accent="danger" if al["now"] else "ok",
      icon='<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M10 21a2 2 0 004 0"/></svg>')}
</section>'''


# ---------------------------------------------------------------------------
# Overview panels
# ---------------------------------------------------------------------------


def _needs_attention(snapshot: dict) -> str:
    """Compact alerts panel — top criticals + warnings, severity-sorted.

    Limits to the first 8 NEW alerts so the Overview tab stays scannable;
    full list lives on the Sites tab via the alert-count badges.
    """
    alerts = snapshot.get("alerts", [])
    new_alerts = sorted([a for a in alerts if a["state"] == "new"],
                        key=lambda a: SEVERITY_ORDER.get(a["severity"], 9))
    ongoing = sum(1 for a in alerts if a["state"] == "ongoing")
    resolved = sum(1 for a in alerts if a["state"] == "resolved")
    muted = sum(1 for a in alerts if a["state"] == "muted")
    total_new = len(new_alerts)
    shown = new_alerts[:8]

    # Build site_key -> account_name lookup so each alert can show which
    # WPE server (nkpmedical1-6) it lives on. Operator no longer has to
    # click through to the site page to know "is this on the box that's
    # at capacity?" CF-only sites and sites without a wpe block render
    # without the chip rather than showing an empty placeholder.
    site_to_account: dict[str, str] = {}
    for s in snapshot.get("sites", []):
        wpe = s.get("wpe") or {}
        acct = wpe.get("account_name")
        if acct:
            site_to_account[s["key"]] = acct

    if shown:
        items = []
        for a in shown:
            sk = a["site_key"]
            # Fleet-level alerts (e.g. analytics_token_failure) use site_key="fleet"
            # and have no per-site page — keep them as plain text. Real sites get
            # wrapped so the operator can click straight into the site's detail.
            if sk and sk != "fleet":
                site_html = (f'<a class="alert-site" '
                             f'href="sites/{_safe_key(sk)}.html">'
                             f'{_esc(sk)}</a>')
            else:
                site_html = f'<span class="alert-site">{_esc(sk or "fleet")}</span>'
            # WPE server chip — only shown when this site has a wpe block
            # in the snapshot. Fleet-level + CF-only sites skip it.
            acct = site_to_account.get(sk)
            account_html = (f'<span class="alert-account">{_esc(acct)}</span>'
                            if acct else "")
            items.append(
                f'<li class="alert sev-{_esc(a["severity"])}">'
                f'<span class="sev-pill sev-{_esc(a["severity"])}">{_esc(a["severity"])}</span>'
                f'{site_html}'
                f'{account_html}'
                f'<span class="alert-rule">{_esc(a["rule"])}</span>'
                f'<span class="alert-summary">{_esc(a["summary"])}</span></li>')
        body = f'<ul class="alert-list">{"".join(items)}</ul>'
        more = (f'<p class="alert-more muted">+ {total_new - len(shown)} more new alerts &mdash; '
                f'see <a class="link-inline" onclick="showTab(1,document.querySelectorAll(\'nav button\')[1])">Sites</a></p>'
                if total_new > len(shown) else "")
    else:
        total = snapshot.get("roster_summary", {}).get("total", 0)
        body = (
            '<div class="all-clear"><span class="all-clear-mark">&#10003;</span>'
            f'<div><strong>All clear</strong>'
            f'<span class="muted block">{total} sites checked, last run '
            f'{_esc(snapshot.get("date", "?"))}, nothing new.</span></div></div>')
        more = ""

    badge_cls = "danger" if shown else "ok"
    return f"""
    <section class="panel attention">
      <div class="panel-head">
        <h2>Needs Attention</h2>
        <span class="count-badge {badge_cls}">{total_new} NEW</span>
        <span class="lifecycle-line muted">{ongoing} ongoing &middot; {resolved} resolved &middot; {muted} muted</span>
      </div>
      {body}{more}
    </section>"""


def _fleet_rollup(snapshot: dict, timeseries_rows: list[dict]) -> str:
    """Per-account bandwidth bars with Δ-vs-prior-snapshot label on each bar."""
    acct = _account_bandwidth(snapshot)
    if not acct:
        return ('<section class="panel"><div class="panel-head"><h2>Fleet Rollup</h2></div>'
                '<p class="muted">No WPE bandwidth data in this snapshot.</p></section>')

    totals_by_date = _account_totals_by_date(timeseries_rows)
    today = snapshot.get("date")
    earlier_dates = sorted(d for d in totals_by_date if d < (today or ""))
    prev_acct = totals_by_date[earlier_dates[-1]] if earlier_dates else {}
    prev_date = earlier_dates[-1] if earlier_dates else None

    peak = max(acct.values()) or 1
    bars = []
    for a, gb in sorted(acct.items(), key=lambda kv: -kv[1]):
        pct = gb / peak * 100
        prev_gb = prev_acct.get(a)
        if prev_gb is None or prev_gb <= 0:
            delta_html = '<span class="bar-delta muted">&mdash;</span>'
        else:
            delta = gb - prev_gb
            pct_change = delta / prev_gb * 100
            if abs(delta) < 0.5:
                cls, arrow = "delta-flat", "&middot;"
            elif delta < 0:
                cls, arrow = "delta-good", "&darr;"
            else:
                cls, arrow = "delta-bad", "&uarr;"
            delta_html = (f'<span class="bar-delta {cls}">{arrow} '
                          f'{abs(delta):,.0f} GB ({pct_change:+.1f}%)</span>')
        bars.append(
            f'<div class="bar-row"><span class="bar-label">{_esc(a)}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{pct:.1f}%">'
            f'</span></span><span class="bar-value">{gb:,.0f} GB</span>'
            f'{delta_html}</div>')
    rs = snapshot.get("roster_summary", {})
    delta_note = (f' &middot; &Delta; vs <strong>{_esc(prev_date)}</strong>'
                  if prev_date else '')
    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>Fleet Rollup &mdash; bandwidth by WPE account</h2>
      </div>
      <div class="bars">{"".join(bars)}</div>
      <p class="muted rollup-foot">{rs.get('wpe+cf', 0)} WPE+CF &middot;
        {rs.get('wpe-only', 0)} WPE-only &middot; {rs.get('cf-only', 0)} CF-only{delta_note}</p>
    </section>"""


def _storage_row(used_gb: float | None, cap_gb: float | None) -> str:
    """Compact secondary progress bar for storage usage, rendered below the
    bandwidth bar on each plan card.

    Returns '' when either the cap or the usage is unknown — keeps the card
    silent rather than guessing. Severity colours match the bandwidth bar's
    80/95 thresholds so an operator reading both bars sees one mental model.
    """
    if cap_gb is None or used_gb is None or cap_gb <= 0:
        return ""
    pct = (used_gb / float(cap_gb)) * 100
    bar_pct = min(pct, 100)
    if pct >= 95:
        cls = "sev-critical"
    elif pct >= 80:
        cls = "sev-warning"
    else:
        cls = ""
    headroom = max(float(cap_gb) - used_gb, 0)
    return (
        f'<div class="plan-storage-row {cls}">'
        f'<div class="plan-storage-head">'
        f'<span class="muted">storage</span>'
        f'<span><strong>{used_gb:,.0f}</strong> of {cap_gb:,.0f} GB '
        f'&middot; <strong>{pct:.0f}%</strong> '
        f'<span class="muted">&middot; headroom {headroom:,.0f} GB</span></span>'
        f'</div>'
        f'<div class="plan-bar-track plan-bar-track-sm">'
        f'<span class="plan-bar-fill" style="width:{bar_pct:.1f}%"></span>'
        f'</div></div>')


def _plan_utilization_panel(today, plans: dict, daily_rows: list[dict],
                            snapshot: dict) -> str:
    """Per-account plan utilization. Every value is source-labeled.

    Configured accounts get a bar, cycle-to-date GB / limit, projection, and
    optional $ overage line when overage_per_gb_usd is set.
    Unconfigured accounts get a placeholder row with current 30-day rolling
    consumption from the snapshot and a "configure to enable" CTA.
    """
    # Build per-account current 30d rolling bandwidth + per-account total
    # storage from the snapshot. Both flow through the alias-aware lookup
    # below so cards keyed on a sanitized label still join to real-name data.
    # Storage uses the pre-aggregated `wpe.storage_gb` field on the snapshot
    # (a point-in-time level, not a cycle sum). Falls back to _latest_storage_gb
    # for snapshots collected before that field was added.
    rolling_by_account: dict[str, float] = {}
    storage_by_account: dict[str, float] = {}
    for s in snapshot.get("sites", []):
        wpe = s.get("wpe") or {}
        acct = wpe.get("account_name") or wpe.get("account")
        if not acct:
            continue
        bw = wpe.get("bandwidth_gb_30d")
        if bw is not None:
            rolling_by_account[acct] = rolling_by_account.get(acct, 0) + bw
        storage = wpe.get("storage_gb")
        if storage is None:
            storage = _latest_storage_gb(wpe)
        if storage is not None:
            storage_by_account[acct] = storage_by_account.get(acct, 0) + storage

    if not plans and not rolling_by_account:
        return ('<section class="panel"><div class="panel-head">'
                '<h2>Plan Utilization</h2></div>'
                '<p class="muted">No WPE plan configuration loaded. Edit '
                '<code>config/wpe-plans.yml</code> to enable cycle-aware utilization.'
                '</p></section>')

    # Collapse the (display_label, real_account_names) graph so one card is
    # rendered per *plan*, even when the YAML aliases multiple real WPE names
    # onto a sanitized label. Real WPE names that have no plan entry surface
    # under their own raw name so unconfigured accounts still show rolling
    # consumption.
    # When a plan was constructed directly (e.g. in unit tests), display_label
    # is empty — fall back to the dict key so each plan still maps to a
    # distinct card.
    plans_by_label: dict[str, AccountPlan] = {}
    label_of_key: dict[str, str] = {}
    for k, p in plans.items():
        label = p.display_label or k
        label_of_key[k] = label
        plans_by_label.setdefault(label, p)
    rolling_by_label: dict[str, float] = {}
    for raw, bw in rolling_by_account.items():
        label = label_of_key.get(raw, raw)
        rolling_by_label[label] = rolling_by_label.get(label, 0) + bw
    storage_by_label: dict[str, float] = {}
    for raw, st in storage_by_account.items():
        label = label_of_key.get(raw, raw)
        storage_by_label[label] = storage_by_label.get(label, 0) + st

    all_accounts = sorted(set(plans_by_label.keys()) | set(rolling_by_label.keys()))
    rows = []
    summary_critical = summary_warning = summary_unconfigured = 0

    for account in all_accounts:
        plan = plans_by_label.get(account) or AccountPlan(display_label=account)
        rolling = rolling_by_label.get(account)
        # The snapshot/daily rows are keyed by the real WPE account name. When
        # the YAML aliases this label, route the data lookup through the real
        # name; otherwise the label IS the real name (back-compat).
        lookup = primary_lookup_name(plan) if plan.real_account_names else account
        # Storage row gets pre-rendered once so both branches below can splice
        # it under their bandwidth content. Independent of cycle_start_day —
        # storage is a current snapshot not a cycle metric.
        storage_html = _storage_row(storage_by_label.get(account),
                                    plan.storage_gb_limit)

        if not account_is_configured(plan):
            summary_unconfigured += 1
            # Partial-config case: when bandwidth_gb_limit IS known (typically
            # auto-fetched from /accounts/{id}/limits) but cycle_start_day is
            # still null, surface the cap + rolling-vs-cap progress so the
            # operator gets immediate value from the auto-fetch. The cycle
            # math (% of cycle elapsed, projection, overage $) still needs
            # cycle_start_day, so we label this clearly as "approximate".
            cap = plan.bandwidth_gb_limit
            rolling_html = (
                f'<span class="row-num">{rolling:,.0f} <small>GB</small></span>'
                if rolling is not None else
                '<span class="row-num muted">&mdash;</span>')
            if cap and rolling is not None:
                pct = (rolling / float(cap)) * 100
                bar_pct = min(pct, 100)
                head_state = (f'rolling 30d &middot; '
                              f'<strong>{pct:.0f}%</strong> of plan')
                body_extra = (
                    f'<div class="plan-bar-track">'
                    f'<span class="plan-bar-fill" style="width:{bar_pct:.1f}%"></span>'
                    f'</div>'
                    f'<div class="plan-row-body">'
                    f'<span>rolling 30d <strong>{rolling:,.0f}</strong> of '
                    f'{cap:,.0f} GB plan</span>'
                    f'<span class="muted">&middot; auto-fetched from '
                    f'<code>/accounts/{{id}}/limits</code></span></div>')
                foot = (f'<span>add <code>cycle_start_day</code> in '
                        f'<code>config/wpe-plans.yml</code> for '
                        f'cycle-accurate % + projection</span>')
            elif cap:
                head_state = f'{cap:,.0f} GB plan &middot; awaiting data'
                body_extra = (f'<div class="plan-row-body">'
                              f'<span class="muted">current 30-day rolling</span>'
                              f'{rolling_html}</div>')
                foot = (f'<span>add <code>cycle_start_day</code> in '
                        f'<code>config/wpe-plans.yml</code> for cycle math</span>')
            else:
                head_state = '<span class="muted">plan limit not set</span>'
                body_extra = (f'<div class="plan-row-body">'
                              f'<span class="muted">current 30-day rolling</span>'
                              f'{rolling_html}</div>')
                foot = (f'<span>source: WPE /installs/usage (rolling 30d)</span>'
                        f'<span>[add <code>cycle_start_day</code> + '
                        f'<code>bandwidth_gb_limit</code> in '
                        f'<code>config/wpe-plans.yml</code>]</span>')
            rows.append(f"""
            <div class="plan-row plan-row-unset">
              <div class="plan-row-head">
                <span class="plan-account">{_esc(lookup)}</span>
                <span class="plan-state">{head_state}</span>
              </div>
              {body_extra}
              {storage_html}
              <div class="plan-row-foot muted">{foot}</div>
            </div>""")
            continue

        # Configured — do the cycle math.
        cycle_start, cycle_end, day_n, cycle_length = cycle_window(
            today, plan.cycle_start_day)
        used_gb = cycle_to_date_gb(lookup, daily_rows, cycle_start, today)
        limit = float(plan.bandwidth_gb_limit)
        pct_used = (used_gb / limit) * 100 if limit > 0 else 0
        # Projection denominator MUST match plan_utilization._eval_axis or
        # the alert engine and the dashboard will disagree on whether an
        # account is on track to bust its plan. Both use observed data_days
        # (not calendar day_n) and require MIN_PROJECTION_DAYS observations.
        data_days = count_data_days(lookup, daily_rows, cycle_start, today)
        projection_active = data_days >= MIN_PROJECTION_DAYS
        projected = (used_gb / data_days) * cycle_length if projection_active else 0.0
        projected_pct = (projected / limit) * 100 if projection_active and limit > 0 else 0.0
        headroom = max(limit - used_gb, 0)
        overage_gb = max(projected - limit, 0)

        if pct_used >= 95:
            sev_cls = "sev-critical"
            summary_critical += 1
        elif pct_used >= 80:
            sev_cls = "sev-warning"
            summary_warning += 1
        else:
            sev_cls = "sev-good"

        cost_html = ""
        if plan.overage_per_gb_usd is not None and overage_gb > 0:
            cost = overage_gb * plan.overage_per_gb_usd
            cost_html = (
                f'<span class="plan-cost">&middot; ${cost:,.2f} projected overage</span>')

        bar_pct = min(pct_used, 100)

        if projection_active:
            head_state = (
                f'{pct_used:.0f}% cycle-to-date &middot; proj {projected_pct:.0f}%')
            proj_overlay_html = (
                f'<span class="plan-bar-proj" '
                f'style="left:{min(projected_pct, 100):.1f}%"></span>')
            projected_html = (
                f'<span class="muted">&middot; projected '
                f'<strong>{projected:,.0f}</strong> GB</span>')
        else:
            head_state = (
                f'{pct_used:.0f}% cycle-to-date &middot; '
                f'<span class="muted">projection: need {MIN_PROJECTION_DAYS}+ data days '
                f'(have {data_days})</span>')
            proj_overlay_html = ""
            projected_html = (
                f'<span class="muted">&middot; projection unavailable '
                f'({data_days} data day{"s" if data_days != 1 else ""})</span>')

        rows.append(f"""
        <div class="plan-row {sev_cls}">
          <div class="plan-row-head">
            <span class="plan-account">{_esc(lookup)}</span>
            <span class="plan-state">{head_state}</span>
          </div>
          <div class="plan-bar-track">
            <span class="plan-bar-fill" style="width:{bar_pct:.1f}%"></span>
            {proj_overlay_html}
          </div>
          <div class="plan-row-body">
            <span>cycle-to-date <strong>{used_gb:,.0f}</strong> of {limit:,.0f} GB</span>
            <span class="muted">&middot; headroom {headroom:,.0f} GB</span>
            {projected_html}
            <span class="muted">&middot; {day_n} of {cycle_length} days into cycle</span>
            {cost_html}
          </div>
          {storage_html}
          <div class="plan-row-foot muted">
            source: WPE /installs/usage ({data_days} of {day_n} day{"s" if day_n != 1 else ""} observed &middot; cycle {cycle_start.isoformat()} &rarr; {cycle_end.isoformat()}) &middot; limit from <code>wpe-plans.yml</code>
          </div>
        </div>""")

    summary = (
        f'<p class="plan-summary muted">'
        f'{summary_critical} critical &middot; {summary_warning} warning &middot; '
        f'{summary_unconfigured} unconfigured</p>')

    return f"""
    <section class="panel plan-panel">
      <div class="panel-head">
        <h2>Plan Utilization</h2>
      </div>
      <div class="plan-rows">{"".join(rows)}</div>
      {summary}
    </section>"""


def _bandwidth_linechart_svg(items: list[tuple]) -> str:
    """Auto-scaled SVG line chart of [(date, gb), ...].

    The y-axis fits the data range (with headroom) rather than anchoring at
    zero, so small day-to-day swings are visible. Three labelled gridlines
    keep the scale honest — the reader always sees the real GB range.
    """
    n = len(items)
    if n < 2:
        return ('<div class="lchart"><p class="muted">One snapshot so far '
                '&mdash; the trend line appears once a second snapshot '
                'lands.</p></div>')
    w, h = 760, 210
    pad_l, pad_r, pad_t, pad_b = 56, 14, 16, 26
    plot_w, plot_h = w - pad_l - pad_r, h - pad_t - pad_b
    vals = [gb for _, gb in items]
    vmin, vmax = min(vals), max(vals)
    span = vmax - vmin
    # Headroom keeps the line off the frame; flat data still centres.
    head = span * 0.18 if span > 0 else (vmax * 0.04 or 1.0)
    lo, hi = vmin - head, vmax + head
    rng = hi - lo

    def px(i: int) -> float:
        return pad_l + plot_w * (i / (n - 1))

    def py(v: float) -> float:
        return pad_t + plot_h * (1 - (v - lo) / rng)

    grid = []
    for frac in (0.0, 0.5, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        grid.append(
            f'<line class="lchart-grid" x1="{pad_l}" y1="{gy:.1f}" '
            f'x2="{w - pad_r}" y2="{gy:.1f}"/>'
            f'<text class="lchart-ylab" x="{pad_l - 9}" y="{gy + 3.5:.1f}" '
            f'text-anchor="end">{lo + rng * frac:,.0f}</text>')

    pts = [(px(i), py(gb)) for i, (_, gb) in enumerate(items)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    base = h - pad_b
    area = (f'M{pts[0][0]:.1f},{base:.1f} '
            + " ".join(f'L{x:.1f},{y:.1f}' for x, y in pts)
            + f' L{pts[-1][0]:.1f},{base:.1f} Z')

    dots, hits, xlabs = [], [], []
    for i, (d, gb) in enumerate(items):
        x, y = pts[i]
        last = i == n - 1
        dots.append(
            f'<circle class="lchart-dot{" is-latest" if last else ""}" '
            f'cx="{x:.1f}" cy="{y:.1f}" r="{5 if last else 3.5}"/>')
        hits.append(
            f'<circle class="lchart-hit" cx="{x:.1f}" cy="{y:.1f}" r="13">'
            f'<title>{gb:,.0f} GB · {_esc(d)}</title></circle>')
        xlabs.append(
            f'<text class="lchart-xlab" x="{x:.1f}" y="{h - 7}" '
            f'text-anchor="middle">{_esc(d[5:])}</text>')

    return (
        '<div class="lchart"><svg class="lchart-svg" '
        f'viewBox="0 0 {w} {h}" role="img" '
        'aria-label="Fleet bandwidth line chart">'
        '<defs><linearGradient id="lchartFill" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#1d2027" stop-opacity="0.16"/>'
        '<stop offset="1" stop-color="#1d2027" stop-opacity="0"/>'
        '</linearGradient></defs>'
        f'{"".join(grid)}'
        f'<path class="lchart-area" d="{area}" fill="url(#lchartFill)"/>'
        f'<polyline class="lchart-line" points="{line}"/>'
        f'{"".join(dots)}{"".join(xlabs)}{"".join(hits)}'
        '</svg></div>')


def _bandwidth_chart(timeseries_rows: list[dict]) -> str:
    """Fleet bandwidth per snapshot date as an auto-scaled line chart."""
    by_date = _fleet_total_by_date(timeseries_rows)
    if not by_date:
        return ('<section class="panel chart-card"><div class="panel-head">'
                '<h2>Fleet bandwidth over time</h2></div>'
                '<p class="muted">No history yet &mdash; trends build as snapshots '
                'accumulate.</p></section>')
    items = sorted(by_date.items())                              # [(date, gb), ...]
    latest_gb = items[-1][1]
    latest_date = items[-1][0]
    # Headline trend = latest vs previous snapshot.
    prev_gb = items[-2][1] if len(items) > 1 else None
    if prev_gb and prev_gb > 0:
        delta_pct = (latest_gb - prev_gb) / prev_gb * 100
        arrow, cls = _trend_class(latest_gb - prev_gb, lower_is_better=True)
        headline_trend = (f'<span class="trend {cls}">{arrow} '
                          f'{_format_pct(delta_pct)}</span>')
    else:
        headline_trend = '<span class="trend trend-flat">first snapshot</span>'
    return f"""
    <section class="panel chart-card">
      <div class="panel-head">
        <div>
          <span class="card-eyebrow">Fleet bandwidth over time</span>
          <h2 class="big-stat">{latest_gb:,.0f} <small>GB</small> {headline_trend}</h2>
        </div>
        <div class="legend">
          <span class="legend-item"><span class="dot dot-orange"></span>Latest snapshot</span>
          <span class="legend-item"><span class="dot dot-ink"></span>Daily rollup</span>
        </div>
      </div>
      {_bandwidth_linechart_svg(items)}
      <p class="muted card-foot">Latest snapshot: <strong>{_esc(latest_date)}</strong>
         &middot; hover any point for that day's total.</p>
    </section>"""


def _top_bandwidth_sites(snapshot: dict, n: int = 6) -> list[dict]:
    sites = []
    for s in snapshot.get("sites", []):
        wpe = s.get("wpe") or {}
        bw = wpe.get("bandwidth_gb_30d")
        if bw is None or bw == 0:
            continue
        sites.append({
            "key": s["key"],
            "account": wpe.get("account_name") or wpe.get("account") or "—",
            "bandwidth_gb": bw,
            "alerts": s.get("alerts_count", 0),
        })
    sites.sort(key=lambda x: -x["bandwidth_gb"])
    return sites[:n]


def _top_sites_card(snapshot: dict) -> str:
    top = _top_bandwidth_sites(snapshot, 6)
    if not top:
        return ('<section class="panel list-card"><div class="panel-head">'
                '<h2>Top bandwidth sites</h2></div>'
                '<p class="muted">No bandwidth data.</p></section>')
    rows = []
    for s in top:
        initial = s["key"][:1].upper()
        alert_dot = ('<span class="row-alert"></span>' if s["alerts"] else '')
        rows.append(
            f'<li class="site-row">'
            f'<span class="avatar-circle">{_esc(initial)}</span>'
            f'<div class="row-main">'
            f'<span class="row-title">{_esc(s["key"])}{alert_dot}</span>'
            f'<span class="row-sub muted">{_esc(s["account"])}</span>'
            f'</div>'
            f'<span class="row-num">{s["bandwidth_gb"]:,.0f} <small>GB</small></span>'
            f'</li>')
    return f"""
    <section class="panel list-card">
      <div class="panel-head">
        <h2>Top bandwidth sites</h2>
        <span class="muted">last 30d</span>
      </div>
      <ul class="site-list">{"".join(rows)}</ul>
    </section>"""


def _alerts_trend_line(timeseries_rows: list[dict]) -> str:
    """SVG line chart — fleet alert count over snapshots."""
    by_date = _alerts_per_date(timeseries_rows)
    items = sorted(by_date.items())
    today_count = items[-1][1] if items else 0
    prev_count = items[-2][1] if len(items) > 1 else None
    if prev_count is not None and prev_count > 0:
        delta = today_count - prev_count
        arrow, cls = _trend_class(delta, lower_is_better=True)
        delta_label = f'<span class="trend {cls}">{arrow} {abs(delta):,}</span> vs prior snapshot'
    else:
        delta_label = '<span class="trend trend-flat">building history</span>'

    if len(items) < 2:
        body = ('<p class="muted">Line chart appears after two or more snapshots. '
                'The daily Docker cron will populate this overnight.</p>')
    else:
        # Build an SVG path through the points
        W, H, PAD_L, PAD_R, PAD_T, PAD_B = 540, 160, 30, 16, 16, 26
        peak = max(v for _, v in items) or 1
        n = len(items)
        def x(i): return PAD_L + (W - PAD_L - PAD_R) * (i / max(n - 1, 1))
        def y(v): return PAD_T + (H - PAD_T - PAD_B) * (1 - (v / peak))
        path_pts = [(x(i), y(v)) for i, (_, v) in enumerate(items)]
        # Smooth via Catmull-Rom -> bezier (just slight curve)
        path_d = [f"M {path_pts[0][0]:.1f} {path_pts[0][1]:.1f}"]
        for i in range(1, n):
            mx = (path_pts[i-1][0] + path_pts[i][0]) / 2
            path_d.append(
                f"Q {path_pts[i-1][0]:.1f} {path_pts[i-1][1]:.1f} "
                f"{mx:.1f} {(path_pts[i-1][1]+path_pts[i][1])/2:.1f}")
            path_d.append(
                f"Q {path_pts[i][0]:.1f} {path_pts[i][1]:.1f} "
                f"{path_pts[i][0]:.1f} {path_pts[i][1]:.1f}")
        # Fill underneath
        fill_d = " ".join(path_d) + f" L {path_pts[-1][0]:.1f} {H-PAD_B} L {path_pts[0][0]:.1f} {H-PAD_B} Z"
        # Highlight last point
        last_x, last_y = path_pts[-1]
        labels = []
        for i, (d_, _) in enumerate(items):
            if i == 0 or i == n - 1 or (i % max(1, (n // 5)) == 0):
                labels.append(
                    f'<text x="{x(i):.1f}" y="{H-6}" class="lc-x">{_esc(d_[5:])}</text>')
        pts = "".join(
            f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" class="lc-pt"/>'
            for px, py in path_pts)
        callout_x = max(min(last_x - 36, W - 90), 12)
        callout_y = max(last_y - 30, 6)
        body = f'''
        <svg class="line-chart" viewBox="0 0 {W} {H}" preserveAspectRatio="none"
             aria-label="Fleet alert count over time">
          <defs>
            <linearGradient id="lc-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stop-color="#a78bfa" stop-opacity=".22"/>
              <stop offset="1" stop-color="#a78bfa" stop-opacity="0"/>
            </linearGradient>
          </defs>
          <path d="{fill_d}" fill="url(#lc-fill)" stroke="none"/>
          <path d="{" ".join(path_d)}" class="lc-line" fill="none"/>
          {pts}
          <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="5" class="lc-pt-last"/>
          <g transform="translate({callout_x:.1f},{callout_y:.1f})">
            <rect width="78" height="26" rx="6" class="lc-callout"/>
            <text x="39" y="17" class="lc-callout-text" text-anchor="middle">
              {today_count:,} alerts
            </text>
          </g>
          {"".join(labels)}
        </svg>'''

    return f"""
    <section class="panel line-card">
      <div class="panel-head">
        <div>
          <span class="card-eyebrow">Recent alerts trend</span>
          <h2 class="big-stat">{today_count:,} {delta_label}</h2>
        </div>
      </div>
      {body}
    </section>"""


def _top_5xx_sites(snapshot: dict, n: int = 5) -> list[dict]:
    """Top-N sites by edge-5xx rate over the last 7 days, with the same volume
    floors the alert engine uses (MIN_REQUESTS_7D=1000, MIN_5XX_EVENTS=10).

    Why floors: a 50-request site that returned 1 error is at 2% but means
    nothing; a 5000-request site at 2% means real users hit real failures.
    Mirroring the rule's floors keeps the panel and the alert engine in
    agreement about who counts as a real offender.

    Returns rows sorted by pct desc, ready for the Overview panel.
    """
    from .rules.edge_5xx_rate import MIN_REQUESTS_7D, MIN_5XX_EVENTS
    rows = []
    for s in snapshot.get("sites", []):
        cf_an = (s.get("cf") or {}).get("analytics") or {}
        pct = cf_an.get("pct_5xx_7d")
        req = cf_an.get("requests_7d") or 0
        err = cf_an.get("requests_5xx_7d") or 0
        if (pct is None or req < MIN_REQUESTS_7D
                or err < MIN_5XX_EVENTS):
            continue
        rows.append({
            "key": s["key"],
            "pct": pct,
            "errors": err,
            "requests": req,
            "top_codes": cf_an.get("top_status_codes_7d") or [],
        })
    rows.sort(key=lambda r: r["pct"], reverse=True)
    return rows[:n]


def _top_5xx_card(snapshot: dict) -> str:
    """Compact Overview panel — worst 5 sites by edge-5xx rate over 7d.

    Severity colour matches the alert engine's thresholds (1% warn, 3% crit)
    so an operator reading the panel and an operator reading the alert see
    the same story. Each row links straight to the site's detail page.
    """
    from .rules.edge_5xx_rate import WARN_PCT, CRIT_PCT
    top = _top_5xx_sites(snapshot, n=5)
    if not top:
        return ('<section class="panel">'
                '<div class="panel-head"><h2>Top 5xx offenders &middot; last 7d</h2></div>'
                '<p class="muted">No sites cleared the volume floor '
                '(1,000+ requests AND 10+ 5xx events in the last 7 days). '
                'Either the fleet is healthy or traffic is too sparse to '
                'measure — try again after more data.</p></section>')

    rows_html = []
    for r in top:
        if r["pct"] >= CRIT_PCT:
            cls = "sev-critical"
        elif r["pct"] >= WARN_PCT:
            cls = "sev-warning"
        else:
            cls = "sev-good"
        # Top 3 status codes as a small inline breakdown, e.g. "504=17k 521=1.4k"
        only5xx = [c for c in r["top_codes"]
                   if 500 <= c.get("code", 0) < 600][:3]
        codes_inline = " ".join(
            f'<span class="code-tag">{c["code"]}={_compact_num(c["requests"])}</span>'
            for c in only5xx) or '<span class="muted">no breakdown</span>'
        rows_html.append(
            f'<li class="t5-row">'
            f'<a class="t5-site" href="sites/{_safe_key(r["key"])}.html">'
            f'{_esc(r["key"])}</a>'
            f'<span class="t5-pct {cls}">{r["pct"]:.2f}%</span>'
            f'<span class="t5-count muted">'
            f'{_compact_num(r["errors"])}/{_compact_num(r["requests"])} req</span>'
            f'<span class="t5-codes">{codes_inline}</span>'
            f'</li>')

    return f'''
    <section class="panel">
      <div class="panel-head">
        <h2>Top 5xx offenders &middot; last 7d</h2>
        <span class="muted" style="font-size:11.5px">
          edge errors (incl. CF gateway 5xx) &middot;
          floor 1k req + 10 errors &middot;
          warn at {WARN_PCT}%, critical at {CRIT_PCT}%
        </span>
      </div>
      <ul class="t5-list">{"".join(rows_html)}</ul>
    </section>'''


def _compact_num(n: float | int) -> str:
    """Format a count for tight panel cells: 1500 -> '1.5k', 17753 -> '17.8k'."""
    n = float(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return f"{int(n)}"


def _overview_tab(snapshot: dict, timeseries_rows: list[dict]) -> str:
    plan_html = _plan_utilization_panel(
        today=_date.today(),
        plans=load_plans(fetch_live_limits=True),
        daily_rows=read_daily_all(),
        snapshot=snapshot,
    )
    return f'''
      {_stat_cards(snapshot, timeseries_rows)}
      {_cf_cost_summary(snapshot)}
      {plan_html}
      {_top_5xx_card(snapshot)}
      <div class="grid-2-1">
        {_bandwidth_chart(timeseries_rows)}
        {_top_sites_card(snapshot)}
      </div>
      <div class="grid-1-1">
        {_alerts_trend_line(timeseries_rows)}
        {_fleet_rollup(snapshot, timeseries_rows)}
      </div>
      {_needs_attention(snapshot)}
    '''


def _cf_cost_summary(snapshot: dict) -> str:
    """One-row panel: CF subscription monthly run-rate + per-plan breakdown.

    Honest framing in the subhead: this is a PROJECTION from CF's published
    plan prices, not an invoice. Usage-based costs (Workers, R2 storage,
    Argo bandwidth) are not included yet — they need separate fetchers.
    """
    from .cost import summarize as _cost_summarize
    cs = _cost_summarize(snapshot)
    total = cs["total_monthly_usd"]
    cur = cs["currency"]
    missing_note = (
        f' &middot; <span class="muted">{cs["zone_count_without_plan"]} '
        'zone(s) missing plan info</span>' if cs["zone_count_without_plan"] else ''
    )
    if not cs["by_plan"]:
        return ('<section class="panel"><h3>CF subscription cost</h3>'
                '<p class="muted">No CF plan info available — pipeline has not '
                'yet captured per-zone plan fields.</p></section>')
    chips = []
    for p in cs["by_plan"]:
        chips.append(
            f'<span class="cost-chip">'
            f'<b>{p["count"]}</b> &times; {_esc(p["name"])} '
            f'<span class="muted">{cur if cur != "mixed" else ""}'
            f'{p["monthly_usd"]:,.2f}/mo</span></span>')
    return f'''
      <section class="panel">
        <div class="panel-head">
          <h3>CF subscription cost</h3>
          <div class="muted">projection from CF plan prices &middot;
          usage costs (Workers, R2, Argo) not included{missing_note}</div>
        </div>
        <div class="cost-row">
          <div class="cost-total">
            <div class="cost-total-label">monthly run-rate</div>
            <div class="cost-total-value">{cur if cur != "mixed" else "USD/mixed"}
              {total:,.2f}</div>
            <div class="cost-total-sub muted">
              {cs["zone_count_with_plan"]} of
              {cs["zone_count_with_plan"] + cs["zone_count_without_plan"]}
              zones priced</div>
          </div>
          <div class="cost-chips">{"".join(chips)}</div>
        </div>
      </section>'''


# ---------------------------------------------------------------------------
# Sites tab
# ---------------------------------------------------------------------------


def _latest_storage_gb(wpe: dict) -> float | None:
    """Latest day's total storage (file + database) in GB, or None when no daily data.

    Storage is a point-in-time level, not a 30-day sum — so this is the most
    recent day's value. Returns None (renders as a dash) when the site has no
    dated daily records.
    """
    dated = [d for d in ((wpe or {}).get("daily") or []) if d.get("date")]
    if not dated:
        return None
    latest = max(dated, key=lambda d: d["date"])
    total_bytes = (int(latest.get("storage_file_bytes") or 0)
                   + int(latest.get("storage_database_bytes") or 0))
    return total_bytes / 1e9


def _sites_tab(snapshot: dict) -> str:
    from .rules.edge_5xx_rate import (
        WARN_PCT as _5XX_WARN, CRIT_PCT as _5XX_CRIT,
        MIN_REQUESTS_7D as _5XX_MIN_REQ, MIN_5XX_EVENTS as _5XX_MIN_ERR)
    rows = []
    for s in sorted(snapshot.get("sites", []),
                    key=lambda s: -((s.get("wpe") or {}).get("bandwidth_gb_30d") or 0)):
        wpe = s.get("wpe") or {}
        cf_an = (s.get("cf") or {}).get("analytics") or {}
        ac = s.get("alerts_count", 0)
        badge = (f'<span class="count-badge danger">{ac}</span>' if ac
                 else '<span class="count-badge ok">0</span>')
        account = wpe.get("account_name") or wpe.get("account") or "—"
        storage_gb = _latest_storage_gb(wpe)
        storage_cell = f"{storage_gb:.1f}" if storage_gb is not None else "—"
        # 5xx column — apply the same volume floors the alert engine + Top
        # 5xx panel use. A 1-request site with 1 error is mathematically
        # 100% but the figure is meaningless noise; flagging it red drowns
        # out the actually-broken sites in the table. Below floor: render
        # muted with a "low traffic" tooltip so the operator still sees the
        # value but isn't alarmed.
        pct_5xx = cf_an.get("pct_5xx_7d")
        req_7d = cf_an.get("requests_7d") or 0
        err_7d = cf_an.get("requests_5xx_7d") or 0
        if pct_5xx is None:
            err_cell = '<td class="num muted">—</td>'
        elif req_7d < _5XX_MIN_REQ or err_7d < _5XX_MIN_ERR:
            # Below the volume floor — keep visible but neutralise the colour
            # so it doesn't compete with real signal.
            tip = (f'low traffic: {err_7d:,}/{req_7d:,} req in 7d '
                   f'(floor {_5XX_MIN_REQ:,} req + {_5XX_MIN_ERR} err)')
            err_cell = (f'<td class="num muted"><span class="cell-pill" '
                        f'title="{_esc(tip)}">{pct_5xx:.2f}%</span></td>')
        else:
            if pct_5xx >= _5XX_CRIT:
                cls_5xx = "sev-critical"
            elif pct_5xx >= _5XX_WARN:
                cls_5xx = "sev-warning"
            else:
                cls_5xx = ""
            only5xx = [c for c in (cf_an.get("top_status_codes_7d") or [])
                       if 500 <= c.get("code", 0) < 600][:3]
            codes_str = (", ".join(f'{c["code"]}={c["requests"]:,}'
                                   for c in only5xx) or "—")
            tip = f'{err_7d:,}/{req_7d:,} req in 7d · codes: {codes_str}'
            err_cell = (f'<td class="num"><span class="cell-pill {cls_5xx}" '
                        f'title="{_esc(tip)}">{pct_5xx:.2f}%</span></td>')
        rows.append(
            f'<tr><td class="site"><a href="sites/{_safe_key(s["key"])}.html" '
            f'class="site-link">{_esc(s["key"])}</a></td>'
            f'<td>{_esc(account)}</td>'
            f'<td class="num">{_esc(wpe.get("bandwidth_gb_30d", "—"))}</td>'
            f'<td class="num">{_esc(storage_cell)}</td>'
            f'<td class="num">{_esc(wpe.get("mb_per_visit", "—"))}</td>'
            f'<td class="num">{_esc(wpe.get("billable_visits_30d", "—"))}</td>'
            f'<td class="num">{_esc(cf_an.get("cache_hit_rate", "—"))}</td>'
            f'<td class="num">{_esc(cf_an.get("threats", "—"))}</td>'
            f'{err_cell}'
            f'<td class="join">{_esc(s.get("join_state", "—"))}</td>'
            f'<td class="num">{badge}</td></tr>')
    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>Sites</h2>
        <input id="site-search" type="search" placeholder="Filter sites..."
               oninput="filterSites()">
      </div>
      <div class="table-wrap">
      <table id="sites-table"><thead><tr>
        <th>Site</th><th>Account</th><th class="num">BW GB</th>
        <th class="num">Storage GB</th><th class="num">MB/v</th>
        <th class="num">Visits</th><th class="num">Cache %</th><th class="num">Threats</th>
        <th class="num" title="Edge 5xx rate over last 7 days (incl. CF gateway 5xx)">5xx %</th>
        <th>Join</th><th class="num">Alerts</th>
      </tr></thead><tbody>{"".join(rows)}</tbody></table>
      </div>
      <p class="muted">{len(rows)} sites &middot; click a column header to sort &middot;
        type to filter &middot; hover 5xx % for top status codes</p>
    </section>"""


# ---------------------------------------------------------------------------
# Trends tab
# ---------------------------------------------------------------------------


def _period_comparison_html(by_date: dict[str, float]) -> str:
    """Period-over-period table for fleet bandwidth: 7d / 15d / 30d windows."""
    if not by_date:
        return ""
    today_str = sorted(by_date.keys())[-1]
    rows = []
    for days in (7, 15, 30):
        d = _period_delta(by_date, days)
        if d is None:
            rows.append(
                f'<tr><td>{days} days</td><td colspan="3" class="muted">'
                f'history building &mdash; need a snapshot from ~{days} days ago'
                f'</td></tr>')
            continue
        delta = d["delta_gb"]
        arrow = "&darr;" if delta < 0 else ("&uarr;" if delta > 0 else "&middot;")
        cls = "delta-good" if delta < 0 else ("delta-bad" if delta > 0 else "delta-flat")
        rows.append(
            f'<tr><td>{days} days '
            f'<span class="muted">({_esc(d["prev_date"])} &rarr; {_esc(d["current_date"])})</span></td>'
            f'<td class="num">{d["prev_gb"]:,.0f} GB</td>'
            f'<td class="num">{d["current_gb"]:,.0f} GB</td>'
            f'<td class="num {cls}">{arrow} {abs(delta):,.0f} GB '
            f'({d["delta_pct"]:+.1f}%)</td></tr>')
    return f"""
    <section class="panel">
      <div class="panel-head"><h2>Improving? &mdash; period comparison</h2></div>
      <table class="periods"><thead><tr>
        <th>Window</th><th class="num">Then</th><th class="num">Now</th>
        <th class="num">Change</th>
      </tr></thead><tbody>{"".join(rows)}</tbody></table>
      <p class="muted">Each row: today&#39;s fleet 30d-rolling total vs the same
        metric N days ago. Down (green) = improving, up (red) = regressing.
        Snapshot for {_esc(today_str)}.</p>
    </section>"""


def _trends_tab(timeseries_rows: list[dict]) -> str:
    by_date = _fleet_total_by_date(timeseries_rows)
    if not by_date:
        return ('<section class="panel"><div class="panel-head"><h2>Trends</h2></div>'
                '<p class="muted">No history yet &mdash; trends build as snapshots '
                'accumulate. Come back after a few daily runs.</p></section>')
    peak = max(by_date.values()) or 1
    cols = []
    for d, gb in sorted(by_date.items()):
        h = gb / peak * 100
        cols.append(
            f'<div class="chart-col" title="{_esc(d)}: {gb:,.0f} GB">'
            f'<span class="chart-bar" style="height:{h:.1f}%"></span>'
            f'<span class="chart-x">{_esc(d[5:])}</span></div>')
    fleet_chart_section = f"""
    <section class="panel">
      <div class="panel-head"><h2>Fleet bandwidth over time</h2></div>
      <div class="chart">{"".join(cols)}</div>
      <p class="muted">Total fleet bandwidth per snapshot. Hover a bar for the value.</p>
    </section>"""
    per_account_section = _per_account_lines_chart(
        _per_account_by_date(timeseries_rows))
    return (_period_comparison_html(by_date) + per_account_section
            + fleet_chart_section)


# ---------------------------------------------------------------------------
# Interventions tab
# ---------------------------------------------------------------------------


def _intervention_aggregate(rows: list[dict]) -> str:
    """Aggregate-learning panel — confirmed interventions grouped by type."""
    from statistics import median
    supported = [r for r in rows if r.get("supported")]
    if not supported:
        return ""
    by_type: dict[str, list[dict]] = {}
    for r in supported:
        by_type.setdefault(r["type"], []).append(r)
    lines = []
    for typ in sorted(by_type):
        group = by_type[typ]
        deltas_30 = [g["horizons"][30]["delta_pct"] for g in group
                     if g["horizons"][30]["delta_pct"] is not None]
        verdicts_30 = [g["horizons"][30]["verdict"] for g in group]
        tally = {}
        for v in verdicts_30:
            tally[v] = tally.get(v, 0) + 1
        tally_str = " / ".join(f"{n} {v}" for v, n in sorted(tally.items()))
        if deltas_30:
            med = f"30d median {median(deltas_30):+.1f}%"
        else:
            med = "no settled 30d data yet"
        lines.append(
            f'<li><strong>{_esc(typ)}</strong> &middot; {len(group)} fixes '
            f'&middot; {_esc(med)} &middot; {_esc(tally_str)}</li>')
    return ('<section class="panel"><div class="panel-head">'
            '<h2>What works &mdash; by fix type</h2></div>'
            f'<ul class="agg-list">{"".join(lines)}</ul></section>')


def _verdict_cell(h: dict) -> str:
    """One horizon cell — a verdict badge with the delta when present."""
    verdict = h.get("verdict", "")
    delta = h.get("delta_pct")
    label = verdict.replace("_", " ")
    if delta is not None:
        label += f" {delta:+.0f}%"
    return f'<td><span class="iv-verdict iv-{_esc(verdict)}">{_esc(label)}</span></td>'


def _interventions_tab(view: dict) -> str:
    """The Interventions tab — needs-review banner + aggregate + per-fix table."""
    rows = view.get("rows", [])
    needs = view.get("needs_review", 0)

    banner = ""
    if needs:
        banner = (f'<div class="iv-banner">{needs} intervention(s) awaiting '
                  f'review in <code>config/interventions.yml</code></div>')

    if not rows:
        return f"""
    <section class="panel">
      <div class="panel-head"><h2>Interventions</h2></div>
      {banner}
      <p class="muted">No confirmed interventions yet. CF config drift is
        auto-drafted into <code>config/interventions.yml</code> with
        <code>status: needs_review</code> &mdash; confirm a draft there (set
        <code>status: confirmed</code>) and its before/after effect will be
        tracked here.</p>
    </section>"""

    body = []
    for r in sorted(rows, key=lambda r: r.get("applied_date", ""), reverse=True):
        if not r.get("supported", True):
            cells = ('<td colspan="3" class="muted">metric not supported in '
                     'v1</td>')
        else:
            h = r["horizons"]
            cells = (_verdict_cell(h[7]) + _verdict_cell(h[30])
                     + _verdict_cell(h[90]))
        body.append(
            f'<tr><td class="site">{_esc(r["site"])}</td>'
            f'<td>{_esc(r["applied_date"])}</td>'
            f'<td>{_esc(r["type"])}</td>'
            f'<td>{_esc(r["target_metric"])}</td>{cells}</tr>')

    return f"""
    {_intervention_aggregate(rows)}
    <section class="panel">
      <div class="panel-head"><h2>Interventions</h2></div>
      {banner}
      <div class="table-wrap">
      <table><thead><tr>
        <th>Site</th><th>Applied</th><th>Type</th><th>Target metric</th>
        <th>7d</th><th>30d</th><th>90d</th>
      </tr></thead><tbody>{"".join(body)}</tbody></table>
      </div>
      <p class="muted">Verdict per horizon: target metric averaged 14 days
        before the fix vs 7 / 30 / 90 days after. Every value traces to real
        daily metrics; sparse windows show <em>too early</em>.</p>
    </section>"""


# ---------------------------------------------------------------------------
# R2 Health tab — read scripts/monitor_r2_health.py's daily JSON output and
# render a table of every R2-offloaded site with broken-thumb count + status.
# Data is produced by a LOCAL cron (scanner needs WPE SSH) and pushed to R2
# by that script; pull_from_r2() restores it into the data tree, so the
# render here is a pure read of the local JSON file. See r2_state inventory.
# ---------------------------------------------------------------------------


def _r2_health_load() -> dict | None:
    """Read data/reports/r2-health/latest.json. Returns None if missing."""
    from .models import ROOT
    path = ROOT / "data" / "reports" / "r2-health" / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _r2_health_status(result: dict) -> str:
    """Classify a per-site result into the operator-facing status label.

    Mirrors sync_r2_health_to_sheet.py's derivation so the dashboard and the
    Google Sheet show the same label for the same site:
      - broken_count > 0 -> needs_resync
      - probed == 0      -> no_recent_upload
      - else             -> clean
    Scanner failures (status != "ok") surface as scan_failed.
    """
    if result.get("status") != "ok":
        return "scan_failed"
    if result.get("broken_count", 0) > 0:
        return "needs_resync"
    if result.get("probed", 0) == 0:
        return "no_recent_upload"
    return "clean"


_R2_HEALTH_CSS_CLASS = {
    "needs_resync": "r2h-bad",
    "scan_failed": "r2h-bad",
    "no_recent_upload": "r2h-warn",
    "clean": "r2h-ok",
}


def _r2_health_tab(payload: dict | None) -> str:
    """The R2 Health tab — per-site offload health from the daily scanner."""
    if not payload:
        return ('<section class="panel"><div class="panel-head">'
                '<h2>R2 Health</h2></div>'
                '<p class="muted">No R2 health scan available yet. Run '
                '<code>python scripts/monitor_r2_health.py</code> locally; '
                'it pushes the result to R2 and this tab will populate on '
                'the next dashboard render.</p></section>')

    results = payload.get("results") or []
    totals = payload.get("totals") or {}
    scan_date = payload.get("date") or "unknown"
    days_window = payload.get("days_window", 30)

    # Sort: broken first, then no_recent_upload, then clean — most actionable
    # rows on top regardless of alphabetical apex.
    def _sort_key(r: dict):
        status = _r2_health_status(r)
        order = {"needs_resync": 0, "scan_failed": 1,
                 "no_recent_upload": 2, "clean": 3}.get(status, 4)
        return (order, -(r.get("broken_count") or 0), r.get("apex") or "")

    rows_html = []
    for r in sorted(results, key=_sort_key):
        status = _r2_health_status(r)
        cls = _R2_HEALTH_CSS_CLASS.get(status, "")
        broken_ids = ", ".join(str(i) for i in (r.get("broken_ids") or [])[:5])
        if len(r.get("broken_ids") or []) > 5:
            broken_ids += f" +{len(r['broken_ids']) - 5}"
        broken_pct = ""
        probed = r.get("probed", 0)
        if probed > 0 and r.get("broken_count", 0) > 0:
            broken_pct = f"{(r['broken_count'] / probed) * 100:.0f}%"
        rows_html.append(
            f'<tr class="{cls}">'
            f'<td class="site">{_esc(r.get("apex", "—"))}</td>'
            f'<td>{_esc(r.get("install", "—"))}</td>'
            f'<td>{_esc(r.get("source", "—"))}</td>'
            f'<td class="num">{_esc(r.get("probed", 0))}</td>'
            f'<td class="num">{_esc(r.get("broken_count", 0))}</td>'
            f'<td class="num">{_esc(broken_pct or "—")}</td>'
            f'<td><span class="sev-pill r2h-{status}">{_esc(status)}</span></td>'
            f'<td class="muted">{_esc(broken_ids or "—")}</td>'
            f'</tr>')

    sites_with_broken = totals.get("sites_with_broken", 0)
    summary_pill = (
        f'<span class="pill r2h-bad-pill">{sites_with_broken} sites need resync</span>'
        if sites_with_broken else
        '<span class="pill r2h-ok-pill">All offloaded sites clean</span>')

    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>R2 Health &mdash; offloaded sites</h2>
        {summary_pill}
      </div>
      <p class="muted">
        {totals.get("sites_scanned", len(results))} sites scanned &middot;
        {totals.get("total_probed", 0)} URLs probed &middot;
        {totals.get("total_broken", 0)} broken across
        {sites_with_broken} site(s) &middot;
        last scan <strong>{_esc(scan_date)}</strong>
        (window: last {_esc(days_window)} days)
      </p>
      <div class="table-wrap">
      <table id="r2-health-table"><thead><tr>
        <th>Apex</th><th>Install</th><th>Source</th>
        <th class="num">Probed ({_esc(days_window)}d)</th>
        <th class="num">Broken</th>
        <th class="num">Broken %</th>
        <th>Status</th>
        <th>Broken IDs (first 5)</th>
      </tr></thead><tbody>{"".join(rows_html)}</tbody></table>
      </div>
      <p class="muted">
        Source: <code>scripts/monitor_r2_health.py</code> (server-side PHP scan
        of recent image attachments via WPE SSH, run daily and pushed to R2 at
        <code>fleet/r2-health/latest.json</code>). To resync a site, run
        <code>python scripts/fix_r2_broken_fleet.py --site &lt;apex&gt;</code>.
      </p>
    </section>"""


# ---------------------------------------------------------------------------
# Changelog tab
# ---------------------------------------------------------------------------


def _changelog_tab(snapshot: dict) -> str:
    drift = [a for a in snapshot.get("alerts", []) if a["rule"] == "config_drift"]
    if not drift:
        return ('<section class="panel"><div class="panel-head">'
                '<h2>Config Changelog</h2></div>'
                '<p class="muted">No Cloudflare config changes detected this run.</p>'
                '</section>')
    rows = []
    for a in drift:
        attr = a["detail"].get("attribution", "—")
        rows.append(
            f'<tr><td>{_esc(snapshot["date"])}</td>'
            f'<td class="site">{_esc(a["site_key"])}</td>'
            f'<td>{_esc(a["summary"])}</td>'
            f'<td><span class="sev-pill sev-{_esc(a["severity"])}">'
            f'{_esc(a["severity"])}</span></td>'
            f'<td><span class="attr-pill attr-{_esc(attr)}">{_esc(attr)}</span></td></tr>')
    return f"""
    <section class="panel">
      <div class="panel-head">
        <h2>Config Changelog &mdash; Cloudflare drift this run</h2>
      </div>
      <div class="table-wrap">
      <table><thead><tr>
        <th>Date</th><th>Site</th><th>Change</th><th>Severity</th><th>By</th>
      </tr></thead><tbody>{"".join(rows)}</tbody></table>
      </div>
    </section>"""


# ---------------------------------------------------------------------------
# Design system  (inline CSS — no external assets)
# ---------------------------------------------------------------------------


_CSS = """
:root{
  /* Light, calm, modern.  Lime-green accent like the Rexora reference. */
  --bg:#f6f7f9;
  --bg-warm:#f3f1ec;
  --card:#ffffff;
  --border:#ececef;
  --border-strong:#dadce0;
  --ink:#14151a;
  --ink-2:#2a2c33;
  --muted:#6a6f78;
  --muted-2:#9aa0aa;
  --accent:#c8f250;      /* lime active pill */
  --accent-ink:#14151a;  /* dark text on the lime */
  --accent-2:#0f1115;    /* nav rail dark */
  --good:#16a34a;
  --good-bg:#eafbe9;
  --bad:#dc2626;
  --bad-bg:#fef2f2;
  --warn:#d97706;
  --warn-bg:#fef7e6;
  --info:#2563eb;
  --info-bg:#eef4ff;
  --crit:#dc2626;
  --crit-bg:#fef2f2;
  --ok:#16a34a;
  --ok-bg:#eafbe9;
  --chart-1:#ff6a3d;     /* selected/highlight orange */
  --chart-ink:#1a1d23;
  --chart-line:#7c3aed;
  --sh-sm: 0 1px 2px rgba(15,17,21,.04);
  --sh:    0 1px 2px rgba(15,17,21,.04), 0 1px 3px rgba(15,17,21,.06);
  --sh-lg: 0 4px 14px rgba(15,17,21,.07);
  --r-sm:8px;
  --r:12px;
  --r-lg:16px;
  --r-pill:999px;
  --font-sans: "Segoe UI Variable Text", -apple-system, BlinkMacSystemFont,
               "SF Pro Text", "Segoe UI", system-ui, sans-serif;
  --font-display: "Segoe UI Variable Display", -apple-system,
                  BlinkMacSystemFont, "SF Pro Display", "Segoe UI",
                  system-ui, sans-serif;
  --font-mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code",
               Consolas, monospace;
}

*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg);
  color:var(--ink);
  font-family:var(--font-sans);
  font-size:14px;
  line-height:1.5;
  -webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;
  font-feature-settings:"ss01","cv11","tnum";
}
a{color:inherit;text-decoration:none}
a:hover{text-decoration:underline}
.muted{color:var(--muted)}
.muted.block{display:block}

/* ---------- Top bar with pill nav ---------- */
.topbar{
  position:sticky;top:0;z-index:20;
  display:grid;
  grid-template-columns:auto 1fr auto;
  align-items:center;
  gap:24px;
  padding:14px 28px;
  background:var(--bg);
  border-bottom:1px solid var(--border);
}
.brand{display:flex;align-items:center;gap:10px;color:var(--ink);font-weight:700}
.brand-mark{
  width:30px;height:30px;border-radius:9px;
  background:var(--accent-2);
  display:inline-flex;align-items:center;justify-content:center;
  color:var(--accent);font-weight:800;font-size:15px;letter-spacing:.5px;
}
.brand-name{font-family:var(--font-display);font-size:16px;letter-spacing:-.01em}

nav.pill-nav{
  justify-self:center;
  display:flex;align-items:center;gap:2px;
  background:#fff;
  border:1px solid var(--border);
  border-radius:var(--r-pill);
  padding:5px;
  box-shadow:var(--sh-sm);
}
nav.pill-nav button{
  border:0;background:transparent;cursor:pointer;
  padding:8px 18px;border-radius:var(--r-pill);
  font:inherit;font-size:13px;color:var(--muted);
  font-weight:500;
  transition:color .12s,background .12s,box-shadow .15s;
}
nav.pill-nav button:hover{color:var(--ink)}
nav.pill-nav button.active{
  background:var(--accent);color:var(--accent-ink);
  font-weight:700;
  box-shadow:0 1px 0 rgba(15,17,21,.06), inset 0 -1px 0 rgba(15,17,21,.08);
}
nav.pill-nav a.console-link{
  padding:7px 15px;border-radius:var(--r-pill);
  font:inherit;font-size:13px;font-weight:600;
  color:var(--ink);background:transparent;
  border:1.5px solid var(--accent);
  text-decoration:none;margin-left:6px;white-space:nowrap;
  transition:background .12s,color .12s;
}
nav.pill-nav a.console-link:hover{
  background:var(--accent);color:var(--accent-ink);
}

.topbar-right{display:flex;align-items:center;gap:12px}
#refresh-btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:7px 13px;
  background:#fff;
  border:1px solid var(--border);
  border-radius:var(--r-pill);
  color:var(--ink);
  font:inherit;font-size:13px;font-weight:500;
  cursor:pointer;
  box-shadow:var(--sh-sm);
  transition:transform .1s,box-shadow .15s,border-color .15s;
}
#refresh-btn:hover:not(:disabled){border-color:var(--ink);box-shadow:var(--sh)}
#refresh-btn:disabled{opacity:.7;cursor:wait}
#refresh-btn.refresh-error{border-color:var(--bad);color:var(--bad)}
#refresh-btn .glyph{display:inline-block;transform:translateY(.5px)}

.pill{
  display:inline-flex;align-items:center;
  padding:5px 11px;border-radius:var(--r-pill);
  font-size:11.5px;font-weight:700;letter-spacing:.02em;
  text-transform:uppercase;
}
.pill.fresh{background:var(--good-bg);color:var(--good)}
.pill.aging{background:#fef3c7;color:#b45309}
.pill.stale{background:var(--bad-bg);color:var(--bad)}
a.pill{text-decoration:none;cursor:pointer}
a.pill:hover{filter:brightness(0.95)}

.user-chip{
  display:inline-flex;align-items:center;gap:9px;
  padding:5px 11px 5px 6px;
  border:1px solid var(--border);border-radius:var(--r-pill);
  background:#fff;
}
.user-chip .avatar{
  width:26px;height:26px;border-radius:50%;
  background:linear-gradient(135deg,#ffd8b1,#ff9a6a);
  color:#fff;display:inline-flex;align-items:center;justify-content:center;
  font-weight:700;font-size:11px;
}
.user-chip .who{display:flex;flex-direction:column;font-size:12px;line-height:1.15}
.user-chip .who small{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.04em}

/* ---------- Welcome strip ---------- */
.welcome{
  display:flex;align-items:flex-end;justify-content:space-between;gap:24px;
  padding:18px 28px 6px;
  max-width:1280px;margin:0 auto;
}
.welcome h1{
  margin:0 0 4px;
  font-family:var(--font-display);
  font-weight:600;font-size:22px;letter-spacing:-.01em;
}
.welcome h1 .wave{display:inline-block;animation:wave 1.4s ease-in-out 1}
@keyframes wave{
  0%,100%{transform:rotate(0)} 25%{transform:rotate(-14deg)}
  50%{transform:rotate(10deg)} 75%{transform:rotate(-6deg)}
}
.welcome .subtitle{margin:0;color:var(--muted);font-size:13.5px}
.export-btn{
  display:inline-flex;align-items:center;gap:6px;
  padding:8px 14px;
  background:#fff;border:1px solid var(--border);
  border-radius:var(--r);
  color:var(--ink);font:inherit;font-size:13px;font-weight:500;
  cursor:pointer;box-shadow:var(--sh-sm);
}
.export-btn:hover{border-color:var(--ink-2)}

/* ---------- Main grid ---------- */
main.shell{padding:18px 28px 56px;max-width:1280px;margin:0 auto}
.tab{display:none} .tab.active{display:block}

.grid-2-1{
  display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:16px;
}
.grid-1-1{
  display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px;
}

/* ---------- KPI grid (legacy class names retained for tests) ---------- */
.stat-grid{
  display:grid;grid-template-columns:repeat(4,1fr);
  gap:14px;margin-bottom:16px;
}
.stat-card{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:var(--r);
  padding:18px 18px 16px;
  box-shadow:var(--sh-sm);
  display:flex;flex-direction:column;gap:8px;
  transition:transform .12s,box-shadow .15s,border-color .15s;
}
.stat-card:hover{box-shadow:var(--sh);transform:translateY(-1px)}
.stat-card-head{display:flex;justify-content:space-between;align-items:center}
.stat-label{
  font-size:11.5px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em;font-weight:600;
}
.stat-icon{
  width:32px;height:32px;border-radius:9px;
  background:#f3f4f6;color:var(--ink-2);
  display:inline-flex;align-items:center;justify-content:center;
}
.stat-value-row{display:flex;align-items:baseline;gap:6px}
.stat-value{
  font-family:var(--font-display);font-weight:700;
  font-size:28px;letter-spacing:-.015em;color:var(--ink);
  font-variant-numeric:tabular-nums;
}
.stat-unit{color:var(--muted);font-size:13px;font-weight:500}
.stat-sub{font-size:12.5px;color:var(--muted)}
.stat-card-danger{
  background:var(--bad-bg);border-color:#f3c7c7;
}
.stat-card-danger .stat-icon{background:#fff;color:var(--bad)}
.stat-card-danger .stat-value{color:var(--bad)}
.stat-card-ok .stat-icon{background:#fff;color:var(--good)}

/* ---------- Trend badges ---------- */
.trend{
  display:inline-flex;align-items:center;gap:3px;
  font-size:12px;font-weight:600;
  font-variant-numeric:tabular-nums;
}
.trend-good{color:var(--good)}
.trend-bad{color:var(--bad)}
.trend-flat{color:var(--muted)}

/* ---------- Generic card / panel ---------- */
.panel{
  background:var(--card);
  border:1px solid var(--border);
  border-radius:var(--r);
  padding:18px;
  box-shadow:var(--sh-sm);
  margin-bottom:0;
}
.panel + .panel{margin-top:16px}
.grid-2-1 > .panel,.grid-1-1 > .panel{margin-bottom:0}
.cost-row{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin-top:8px}
.cost-total{padding:10px 16px;background:#f8fafc;border:1px solid #e5e7eb;
  border-radius:10px;min-width:180px}
.cost-total-label{font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;
  color:#6a6f78}
.cost-total-value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;
  margin-top:2px}
.cost-total-sub{font-size:11px;margin-top:2px}
.cost-chips{display:flex;gap:8px;flex-wrap:wrap;flex:1}
.cost-chip{font-size:12px;padding:6px 10px;background:#fff;border:1px solid #ececef;
  border-radius:999px;font-variant-numeric:tabular-nums}
.panel-head{
  display:flex;align-items:center;gap:12px;
  margin-bottom:12px;flex-wrap:wrap;
}
.panel-head h2{
  margin:0;font-size:15px;font-weight:600;
  font-family:var(--font-display);letter-spacing:-.005em;
}
.panel-head h2.big-stat{
  font-size:22px;font-weight:700;letter-spacing:-.015em;
  font-variant-numeric:tabular-nums;
}
.panel-head h2.big-stat small{
  font-size:14px;color:var(--muted);font-weight:500;
}
.card-eyebrow{
  display:block;font-size:11.5px;color:var(--muted);
  text-transform:uppercase;letter-spacing:.06em;font-weight:600;
  margin-bottom:2px;
}
.card-foot{margin:10px 0 0;font-size:12.5px}

/* ---------- "Needs Attention" panel ---------- */
.panel.attention{position:relative}
.panel.attention::before{
  content:"";position:absolute;left:0;top:18px;bottom:18px;width:3px;
  border-radius:3px;background:var(--bad);
}
.panel.attention .panel-head{padding-left:10px}
.count-badge{
  padding:3px 10px;border-radius:var(--r-pill);
  font-size:11.5px;font-weight:700;letter-spacing:.03em;
}
.count-badge.danger{background:var(--bad-bg);color:var(--bad)}
.count-badge.ok{background:var(--good-bg);color:var(--good)}
.lifecycle-line{margin-left:auto;font-size:12px}
.alert-list{list-style:none;margin:0;padding:0 0 0 10px}
.alert{
  display:flex;align-items:center;gap:10px;
  padding:9px 12px;border-radius:var(--r-sm);
  margin:5px 0;font-size:13px;
}
.alert.sev-critical{background:var(--crit-bg)}
.alert.sev-warning{background:var(--warn-bg)}
.alert.sev-info{background:var(--info-bg)}
.sev-pill{
  padding:2px 9px;border-radius:var(--r-pill);
  font-size:10.5px;font-weight:700;text-transform:uppercase;
  letter-spacing:.04em;color:#fff;
}
.sev-pill.sev-critical{background:var(--crit)}
.sev-pill.sev-warning{background:var(--warn)}
.sev-pill.sev-info{background:var(--info)}
/* ---------- Storage secondary bar on plan cards ---------- */
.plan-storage-row{margin:8px 0 0;padding-top:8px;
  border-top:1px dashed #e5e7eb}
.plan-storage-head{display:flex;justify-content:space-between;
  font-size:12px;margin-bottom:4px}
.plan-bar-track-sm{height:6px;background:#eef0f3;border-radius:3px;
  overflow:hidden;position:relative}
.plan-bar-track-sm .plan-bar-fill{height:100%;background:linear-gradient(
  90deg,#84cc16,#a3e635);transition:width .3s}
.plan-storage-row.sev-warning .plan-bar-track-sm .plan-bar-fill{
  background:linear-gradient(90deg,#f59e0b,#fbbf24)}
.plan-storage-row.sev-critical .plan-bar-track-sm .plan-bar-fill{
  background:linear-gradient(90deg,#dc2626,#ef4444)}

/* ---------- Top 5xx offenders panel ---------- */
.t5-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.t5-row{display:grid;grid-template-columns:minmax(140px,1.4fr) 64px 130px 1fr;
  align-items:center;gap:12px;padding:8px 10px;border-radius:8px;
  background:#f9fafb;border:1px solid #eef0f3;font-size:13px}
.t5-site{font-weight:600;color:var(--ink);text-decoration:none;font-size:13.5px}
.t5-site:hover{text-decoration:underline}
.t5-pct{font-weight:700;font-family:var(--font-mono);text-align:right;
  padding:2px 8px;border-radius:6px;color:#fff;background:#94a3b8}
.t5-pct.sev-warning{background:var(--warn)}
.t5-pct.sev-critical{background:var(--crit)}
.t5-pct.sev-good{background:#94a3b8}
.t5-count{font-family:var(--font-mono);font-size:11.5px}
.t5-codes{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.code-tag{font-family:var(--font-mono);font-size:11px;padding:1px 6px;
  background:#fff;border:1px solid #e5e7eb;border-radius:4px;color:var(--ink-2)}

/* ---------- inline cell-pill for sortable table cells (5xx %, etc.) ---------- */
.cell-pill{display:inline-block;padding:2px 7px;border-radius:6px;
  font-weight:600;font-family:var(--font-mono);font-size:11.5px}
.cell-pill.sev-warning{background:var(--warn);color:#fff}
.cell-pill.sev-critical{background:var(--crit);color:#fff}

.sev-pill.r2h-needs_resync{background:var(--crit)}
.sev-pill.r2h-scan_failed{background:#7a5a05}
.sev-pill.r2h-no_recent_upload{background:#9ca3af;color:#1f2937}
.sev-pill.r2h-clean{background:#16a34a}
.pill.r2h-bad-pill{background:#fee2e2;color:#991b1b;border:1px solid #fecaca;
  padding:4px 11px;border-radius:999px;font-weight:600;font-size:12px}
.pill.r2h-ok-pill{background:#dcfce7;color:#166534;border:1px solid #bbf7d0;
  padding:4px 11px;border-radius:999px;font-weight:600;font-size:12px}
tr.r2h-bad td{background:#fff5f5}
tr.r2h-warn td{background:#fafafa}
tr.r2h-ok td{background:#f7fdf9}
.alert-site{font-weight:600;color:var(--ink);text-decoration:none}
a.alert-site:hover{text-decoration:underline;cursor:pointer}
.alert-account{display:inline-block;padding:1px 7px;border-radius:4px;
  background:#e0e7ff;color:#3730a3;font-family:var(--font-mono);
  font-size:10.5px;font-weight:600;letter-spacing:.02em}
.alert-rule{color:var(--muted);font-family:var(--font-mono);font-size:11.5px}
.alert-summary{color:var(--ink-2)}
.alert-more{padding-left:10px;margin:6px 0 0;font-size:12.5px}
.link-inline{
  color:var(--info);cursor:pointer;font-weight:600;
}
.all-clear{
  display:flex;align-items:center;gap:14px;padding:18px;
  background:var(--good-bg);border-radius:var(--r-sm);
  margin-left:10px;
}
.all-clear-mark{font-size:28px;color:var(--good);font-weight:700}

/* ---------- Fleet rollup bars ---------- */
.bars{display:flex;flex-direction:column;gap:9px}
.bar-row{display:flex;align-items:center;gap:10px;font-size:13px}
.bar-label{
  width:120px;color:var(--ink);font-weight:500;
  font-variant-numeric:tabular-nums;
}
.bar-track{
  flex:1;background:#f1f3f5;
  border-radius:var(--r-pill);height:14px;overflow:hidden;
}
.bar-fill{
  display:block;height:100%;
  background:linear-gradient(90deg,#cae23b,#9ed420);
  border-radius:inherit;
}
.bar-value{
  width:80px;text-align:right;
  font-family:var(--font-mono);font-size:12.5px;
}
.bar-delta{
  width:170px;text-align:right;
  font-family:var(--font-mono);font-size:11.5px;
}
.rollup-foot{margin:12px 0 0;font-size:12.5px}
.delta-good{color:var(--good);font-weight:600}
.delta-bad{color:var(--bad);font-weight:600}
.delta-flat{color:var(--muted)}

/* ---------- Big bandwidth bar chart ---------- */
.chart-card .panel-head{align-items:flex-start;justify-content:space-between}
.legend{display:flex;gap:12px;align-items:center;color:var(--muted);font-size:12px}
.legend-item{display:inline-flex;align-items:center;gap:6px}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%}
.dot-orange{background:var(--chart-1)}
.dot-ink{background:var(--chart-ink)}
.lchart{position:relative;padding:10px 0 2px}
.lchart-svg{width:100%;height:auto;display:block}
.lchart-grid{stroke:var(--border);stroke-width:1}
.lchart-ylab{fill:var(--muted);font-size:11px;font-family:var(--font-mono)}
.lchart-xlab{fill:var(--muted);font-size:11px}
.lchart-area{stroke:none}
.lchart-line{
  fill:none;stroke:#1d2027;stroke-width:2.5;
  stroke-linejoin:round;stroke-linecap:round;
}
.lchart-dot{fill:#fff;stroke:#1d2027;stroke-width:2}
.lchart-dot.is-latest{fill:#ff6132;stroke:#fff;stroke-width:2.5}
.lchart-hit{fill:transparent;cursor:pointer}

/* ---------- Top sites list ---------- */
.list-card .panel-head{justify-content:space-between}
.site-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.site-row{
  display:flex;align-items:center;gap:12px;
  padding:9px 8px;border-radius:var(--r-sm);
  transition:background .12s;
}
.site-row:hover{background:#f8f9fb}
.avatar-circle{
  width:32px;height:32px;border-radius:9px;
  background:linear-gradient(135deg,#e3f0a8,#cae23b);
  color:var(--ink);font-weight:700;font-size:13px;
  display:inline-flex;align-items:center;justify-content:center;
  flex-shrink:0;
}
.row-main{flex:1;min-width:0;display:flex;flex-direction:column;line-height:1.25}
.row-title{
  font-weight:600;font-size:13.5px;color:var(--ink);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.row-alert{
  display:inline-block;width:7px;height:7px;border-radius:50%;
  background:var(--bad);margin-left:6px;vertical-align:middle;
}
.row-sub{font-size:11.5px}
.row-num{
  font-family:var(--font-mono);font-size:13px;font-weight:600;
  color:var(--ink-2);font-variant-numeric:tabular-nums;
}
.row-num small{color:var(--muted);font-weight:500;font-size:11px}

/* ---------- Line chart (alerts trend) ---------- */
.line-card .panel-head{align-items:flex-start;justify-content:space-between}
.line-chart{width:100%;height:200px;display:block}
.lc-line{stroke:var(--chart-line);stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.lc-pt{fill:#fff;stroke:var(--chart-line);stroke-width:2}
.lc-pt-last{fill:var(--chart-line);stroke:#fff;stroke-width:3}
.lc-x{fill:var(--muted);font-size:10.5px;text-anchor:middle;font-family:var(--font-mono)}
.lc-callout{fill:var(--ink);stroke:#fff;stroke-width:2}
.lc-callout-text{fill:#fff;font-size:11.5px;font-weight:700;font-family:var(--font-mono)}

/* ---------- Sites table & generic tables ---------- */
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{
  text-align:left;padding:9px 12px;
  border-bottom:1px solid var(--border);
}
th{
  background:#fafbfc;cursor:pointer;user-select:none;
  white-space:nowrap;font-size:11px;text-transform:uppercase;
  letter-spacing:.05em;color:var(--muted);font-weight:600;
  position:sticky;top:0;
}
th:hover{color:var(--ink)}
td.num,th.num{text-align:right;font-family:var(--font-mono);font-variant-numeric:tabular-nums}
td.site{font-weight:600}
.site-link{color:inherit;text-decoration:none;border-bottom:1px dotted transparent}
.site-link:hover{border-bottom-color:currentColor;text-decoration:none}
td.join{color:var(--muted);font-size:12px;font-family:var(--font-mono)}
tbody tr:nth-child(even){background:#fcfcfd}
tbody tr:hover{background:#f0f6ff}
#site-search{
  margin-left:auto;
  padding:7px 12px;
  border:1px solid var(--border);
  border-radius:var(--r-pill);
  font:inherit;font-size:13px;width:240px;
  background:#fff;color:var(--ink);
}
#site-search:focus{outline:none;border-color:var(--ink)}

/* ---------- Trends-tab simple bar chart (kept from earlier) ---------- */
.chart{
  display:flex;align-items:flex-end;gap:6px;
  height:200px;padding:10px 0;border-bottom:1px solid var(--border);
}
.chart-col{
  flex:1;display:flex;flex-direction:column;
  align-items:center;justify-content:flex-end;height:100%;
}
.chart-bar{
  width:100%;background:linear-gradient(180deg,#22242b,#0f1115);
  border-radius:5px 5px 0 0;min-height:3px;
}
.chart-col:hover .chart-bar{background:linear-gradient(180deg,#4c4f58,#1d2027)}
.chart-x{font-size:10.5px;color:var(--muted);margin-top:4px;white-space:nowrap}

/* ---------- Changelog pills ---------- */
.attr-pill{
  padding:2px 9px;border-radius:var(--r-pill);
  font-size:11px;font-weight:600;letter-spacing:.02em;
}
.attr-us{background:var(--info-bg);color:var(--info)}
.attr-external{background:var(--crit-bg);color:var(--crit)}
table.periods th,table.periods td{padding:11px 12px}

/* ---------- Plan Utilization panel ---------- */
.plan-panel .plan-rows{display:flex;flex-direction:column;gap:12px}
.plan-row{padding:12px;border:1px solid var(--border);border-radius:var(--r-sm);
  background:var(--card)}
.plan-row.sev-good{border-left:3px solid var(--good)}
.plan-row.sev-warning{border-left:3px solid var(--warn);background:var(--warn-bg)}
.plan-row.sev-critical{border-left:3px solid var(--crit);background:var(--crit-bg)}
.plan-row-unset{border-left:3px solid var(--border-strong);background:#fafbfc}
.plan-row-head{display:flex;justify-content:space-between;align-items:baseline;
  font-size:13.5px;margin-bottom:8px}
.plan-account{font-weight:600;color:var(--ink)}
.plan-state{color:var(--ink-2);font-family:var(--font-mono);font-size:12.5px}
.plan-bar-track{position:relative;height:10px;background:#f1f3f5;border-radius:999px;
  margin:6px 0;overflow:hidden}
.plan-bar-fill{display:block;height:100%;background:linear-gradient(90deg,#cae23b,#9ed420);
  border-radius:999px}
.plan-row.sev-warning .plan-bar-fill{background:linear-gradient(90deg,#fde68a,#d97706)}
.plan-row.sev-critical .plan-bar-fill{background:linear-gradient(90deg,#fecaca,#dc2626)}
.plan-bar-proj{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--ink)}
.plan-row-body{display:flex;flex-wrap:wrap;gap:10px;font-size:12.5px;color:var(--ink-2);
  font-variant-numeric:tabular-nums}
.plan-row-body strong{color:var(--ink)}
.plan-cost{color:var(--bad);font-weight:600}
.plan-row-foot{margin-top:6px;font-size:11.5px}
.plan-row-foot code{background:#f1f3f5;padding:1px 5px;border-radius:4px;
  font-family:var(--font-mono);font-size:11px}
.plan-summary{margin:14px 0 0;font-size:12.5px}

/* ---------- Per-account multi-line chart (Trends tab) ---------- */
.pa-chart{
  width:100%;
  height:auto;
  display:block;
  margin-top:10px;
  background:linear-gradient(180deg,#fafbfc 0%,#ffffff 100%);
  border-radius:var(--r-sm);
}
.pa-grid{
  stroke:var(--border);
  stroke-width:1;
  shape-rendering:crispEdges;
}
.pa-line{
  transition:opacity .15s ease;
}
.pa-chart:hover .pa-line{opacity:.35}
.pa-chart:hover .pa-line:hover{opacity:1;stroke-width:3}
.pa-x,.pa-y{
  font-family:var(--font-mono);
  font-size:10.5px;
  fill:var(--muted);
}
.pa-end{
  font-family:var(--font-sans);
  font-size:11px;
  font-weight:600;
}
.pa-legend{
  display:flex;
  flex-wrap:wrap;
  gap:14px 22px;
  margin-top:12px;
  padding-top:12px;
  border-top:1px solid var(--border);
}
.pa-legend-item{
  display:inline-flex;align-items:center;gap:7px;
  font-size:12.5px;
  font-variant-numeric:tabular-nums;
}
.pa-swatch{
  display:inline-block;
  width:11px;height:11px;border-radius:3px;
  flex:0 0 11px;
}

/* ---------- Responsive ---------- */
@media(max-width:980px){
  .grid-2-1,.grid-1-1{grid-template-columns:1fr}
  .stat-grid{grid-template-columns:repeat(2,1fr)}
  nav.pill-nav{justify-self:start;overflow-x:auto;max-width:100%}
}
@media(max-width:640px){
  .topbar{grid-template-columns:1fr;gap:10px;padding:14px}
  .welcome{padding:14px 14px 6px;flex-direction:column;align-items:flex-start}
  main.shell{padding:14px}
  .stat-grid{grid-template-columns:1fr}
  .user-chip .who{display:none}
}
.iv-banner{background:#fef7e6;border:1px solid #f3e0a3;color:#7a5a05;
  padding:9px 14px;border-radius:8px;margin:0 0 14px;font-size:13px}
.iv-verdict{display:inline-block;padding:2px 9px;border-radius:999px;
  font-size:11.5px;font-weight:600;white-space:nowrap}
.iv-worked{background:#eafbe9;color:#16a34a}
.iv-regressed{background:#fef2f2;color:#dc2626}
.iv-no_effect{background:#f1f2f4;color:#6a6f78}
.iv-too_early,.iv-baseline_unavailable{background:#f1f2f4;color:#9aa0aa}
.agg-list{margin:8px 0 0;padding-left:18px;font-size:13px;line-height:1.8}
"""


_JS = """
async function refreshData(){
  var btn=document.getElementById('refresh-btn');
  if(!btn) return;
  if(location.protocol==='file:'){
    alert('The Refresh button only works when the dashboard is served via the local server.\\n\\n'+
          'Start the server from the repo root:\\n  python -m projects.fleet_monitoring.serve\\n\\n'+
          'Then open http://localhost:8765/ in your browser.');
    return;
  }
  btn.disabled=true; btn.classList.remove('refresh-error');
  btn.querySelector('.label').textContent='Starting...';
  var started=Date.now();
  try{
    await fetch('/refresh',{method:'POST'});
    pollRefreshStatus(btn,started);
  }catch(e){
    btn.querySelector('.label').textContent='Error: '+e;
    btn.classList.add('refresh-error'); btn.disabled=false;
  }
}
async function pollRefreshStatus(btn,started){
  try{
    var r=await fetch('/status');
    var s=await r.json();
    var elapsed=Math.round((Date.now()-started)/1000);
    var label=btn.querySelector('.label');
    if(s.state==='completed'){
      label.textContent='Done in '+elapsed+'s — reloading';
      setTimeout(function(){location.reload();},800);
      return;
    }
    if(s.state==='error'){
      label.textContent='Error: '+(s.message||'unknown').slice(0,70);
      btn.classList.add('refresh-error'); btn.disabled=false;
      return;
    }
    label.textContent='Refreshing... '+elapsed+'s';
    setTimeout(function(){pollRefreshStatus(btn,started);},2000);
  }catch(e){
    btn.querySelector('.label').textContent='Status check failed';
    btn.classList.add('refresh-error'); btn.disabled=false;
  }
}
function showTab(i,btn){
  document.querySelectorAll('.tab').forEach(function(t,j){
    t.classList.toggle('active',i===j);});
  document.querySelectorAll('nav.pill-nav button').forEach(function(b){
    b.classList.remove('active');});
  btn.classList.add('active');
}
function applyHashTab(){
  var names=['overview','sites','trends','changelog','interventions'];
  var i=names.indexOf((location.hash||'').replace('#','').toLowerCase());
  if(i<0) return;
  var btns=document.querySelectorAll('nav.pill-nav button');
  if(btns[i]) showTab(i,btns[i]);
}
window.addEventListener('DOMContentLoaded',applyHashTab);
window.addEventListener('hashchange',applyHashTab);
function filterSites(){
  var q=document.getElementById('site-search').value.toLowerCase();
  document.querySelectorAll('#sites-table tbody tr').forEach(function(r){
    r.style.display=r.textContent.toLowerCase().indexOf(q)>-1?'':'none';
  });
}
document.querySelectorAll('#sites-table th').forEach(function(th,idx){
  th.addEventListener('click',function(){
    var tb=th.closest('table').querySelector('tbody');
    var rows=[].slice.call(tb.rows).filter(function(r){
      return r.style.display!=='none';});
    var num=function(v){
      var n=parseFloat(v.replace(/[^0-9.\\-]/g,''));return isNaN(n)?null:n;};
    var asc=th.dataset.asc==='1';th.dataset.asc=asc?'0':'1';
    rows.sort(function(a,b){
      var x=a.cells[idx].textContent.trim(),y=b.cells[idx].textContent.trim();
      var nx=num(x),ny=num(y);
      var c=(nx!==null&&ny!==null)?nx-ny:x.localeCompare(y);
      return asc?c:-c;
    });
    rows.forEach(function(r){tb.appendChild(r);});
  });
});
"""


# ---------------------------------------------------------------------------
# Shell + entry points
# ---------------------------------------------------------------------------


def render_html(snapshot: dict, timeseries_rows: list[dict],
                interventions_view: dict | None = None) -> str:
    """Build the full dashboard HTML string."""
    fresh_label, fresh_class = _freshness(snapshot.get("captured_at", ""))
    cov = snapshot.get("run", {}).get("coverage", {})
    dur = snapshot.get("run", {}).get("duration_s", 0)
    total_sites = snapshot.get("roster_summary", {}).get("total", 0)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Monitoring &mdash; {_esc(snapshot.get("date", "?"))}</title>
<style>{_CSS}</style></head><body>

<header class="topbar">
  <div class="brand">
    <span class="brand-mark">F</span>
    <span class="brand-name">Fleet Monitor</span>
  </div>
  <nav class="pill-nav" role="tablist">
    <button class="active" onclick="showTab(0,this)">Overview</button>
    <button onclick="showTab(1,this)">Sites</button>
    <button onclick="showTab(2,this)">Trends</button>
    <button onclick="showTab(3,this)">Changelog</button>
    <button onclick="showTab(4,this)">Interventions</button>
    <button onclick="showTab(5,this)">R2 Health</button>
    <a class="console-link" href="console.html">Console &rarr;</a>
  </nav>
  <div class="topbar-right">
    <button id="refresh-btn" onclick="refreshData()" title="Re-run the pipeline (served-from-localhost only)">
      <span class="glyph">&#x21BB;</span>
      <span class="label">Refresh data</span>
    </button>
    <a class="pill {fresh_class}" href="/pipeline" title="open pipeline health">{_esc(fresh_label)}</a>
    <div class="user-chip" title="last run {_esc(snapshot.get('date','?'))} · {_esc(dur)}s · WPE {_esc(cov.get('wpe','?'))} · CF {_esc(cov.get('cf_config','?'))}">
      <span class="avatar">R</span>
      <span class="who"><span>Roland</span><small>Admin</small></span>
    </div>
  </div>
</header>

<section class="welcome">
  <div>
    <h1>Welcome, Roland <span class="wave">&#x1F44B;</span></h1>
    <p class="subtitle">Bandwidth, security, and fleet health across <strong>{total_sites:,}</strong> sites
       &middot; last run <strong>{_esc(snapshot.get('date','?'))}</strong>
       &middot; coverage <strong>WPE {_esc(cov.get('wpe','?'))}</strong> &middot;
       <strong>CF {_esc(cov.get('cf_config','?'))}</strong>
    </p>
  </div>
  <button class="export-btn" onclick="window.print()">Export Report &nbsp;&#x2197;</button>
</section>

<main class="shell">
  <div class="tab active">{_overview_tab(snapshot, timeseries_rows)}</div>
  <div class="tab">{_sites_tab(snapshot)}</div>
  <div class="tab">{_trends_tab(timeseries_rows)}</div>
  <div class="tab">{_changelog_tab(snapshot)}</div>
  <div class="tab">{_interventions_tab(interventions_view or {"needs_review": 0, "rows": []})}</div>
  <div class="tab">{_r2_health_tab(_r2_health_load())}</div>
</main>

<script>{_JS}</script>
</body></html>"""


def _build_interventions_view() -> dict:
    """Assemble the Interventions tab view from fleet.db + interventions.yml.

    Returns {"needs_review": int, "rows": [...]}. Tolerates a missing
    fleet.db (first run before sync) — returns an empty view.
    """
    from .models import FLEET_DB
    from .interventions import load_interventions
    from .effectiveness import VALID_TARGETS
    from . import fleet_db

    try:
        review_q = load_interventions()
    except ValueError:
        review_q = []
    needs_review = sum(1 for i in review_q if i.get("status") == "needs_review")

    if not FLEET_DB.exists():
        return {"needs_review": needs_review, "rows": []}

    ivs = fleet_db.query(
        FLEET_DB,
        "SELECT id, site_key, applied_date, type, target_metric "
        "FROM interventions")
    eff = fleet_db.query(
        FLEET_DB,
        "SELECT intervention_id, horizon_days, delta_pct, verdict "
        "FROM effectiveness")
    eff_by_iv: dict[int, dict] = {}
    for e in eff:
        eff_by_iv.setdefault(e["intervention_id"], {})[e["horizon_days"]] = {
            "verdict": e["verdict"], "delta_pct": e["delta_pct"]}

    rows = []
    for iv in ivs:
        supported = iv["target_metric"] in VALID_TARGETS
        horizons = eff_by_iv.get(iv["id"], {})
        for h in (7, 30, 90):
            horizons.setdefault(h, {"verdict": "too_early", "delta_pct": None})
        rows.append({
            "site": iv["site_key"], "applied_date": iv["applied_date"],
            "type": iv["type"], "target_metric": iv["target_metric"],
            "supported": supported, "horizons": horizons})
    return {"needs_review": needs_review, "rows": rows}


def render() -> str:
    """Load the latest analyzed snapshot + timeseries, write dashboard.html,
    console.html, and per-site pages."""
    snaps = sorted(SNAPSHOTS_DIR.glob("*.json"))
    if not snaps:
        raise SystemExit("No snapshots found. Run collect + analyze first.")
    snapshot = json.loads(snaps[-1].read_text(encoding="utf-8"))
    rows = read_all()
    interventions_view = _build_interventions_view()
    html = render_html(snapshot, rows, interventions_view=interventions_view)
    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    CONSOLE_FILE.write_text(
        render_console(snapshot, rows, interventions_view=interventions_view),
        encoding="utf-8")
    n_sites = write_all_site_pages(snapshot, rows)
    print(f"Dashboard + console written: {DASHBOARD_FILE} "
          f"(+ {n_sites} per-site pages)")
    return str(DASHBOARD_FILE)


def main():
    argparse.ArgumentParser(description="Fleet monitoring — render stage").parse_args()
    try:
        render()
    except Exception as e:
        print(f"RENDER FAILED: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
