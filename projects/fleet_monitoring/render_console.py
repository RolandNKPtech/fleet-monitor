"""Fleet Console — redesigned 3-column console (console.html).

Self-contained single file: dark sidebar + dark account-grouped site list +
a light detail panel filled client-side from embedded per-site JSON.

Standalone by design — this module does NOT import render.py (render.py
imports this one to write console.html; importing back would be circular).
"""
from __future__ import annotations
import html as _html
import json

from .render_site import safe_key as _safe_key

# Alert severities, weighted so the worst site sorts first.
_SEVERITY_WEIGHT = {"critical": 2, "warning": 1, "info": 0}


def _esc(v) -> str:
    return _html.escape("" if v is None else str(v))


def _status_for_site(join_state: str | None, alerts: list[dict]) -> str:
    """Status class for the site-list dot: crit / warn / ok / nodata."""
    severities = {(a.get("severity") or "").lower() for a in alerts}
    if "critical" in severities:
        return "crit"
    if "warning" in severities:
        return "warn"
    if join_state and "wpe" in join_state:
        return "ok"
    return "nodata"


def _storage_series(wpe: dict | None) -> list[dict]:
    """Oldest-first [{date, file_gb, db_gb}] derived from wpe.daily."""
    out = []
    for d in sorted((wpe or {}).get("daily") or [],
                    key=lambda r: r.get("date") or ""):
        if not d.get("date"):
            continue
        out.append({
            "date": d["date"],
            "file_gb": round((d.get("storage_file_bytes") or 0) / 1e9, 2),
            "db_gb": round((d.get("storage_database_bytes") or 0) / 1e9, 2),
        })
    return out


def _cf_config_summary(cf: dict | None) -> dict:
    """Flatten cf.config into the fields the console card shows."""
    cfg = (cf or {}).get("config") or {}
    settings = cfg.get("settings") or {}
    bot = cfg.get("bot") or {}
    waf = cfg.get("waf_rules")
    cache = cfg.get("cache_rules")
    return {
        "tls": settings.get("min_tls_version"),
        "ssl": settings.get("ssl"),
        "waf_count": len(waf) if waf is not None else None,
        "cache_rule_count": len(cache) if cache is not None else None,
        "ai_protection": bot.get("ai_bots_protection"),
        "dns_proxy_www": cfg.get("dns_proxy_www"),
    }


def _site_interventions(key: str,
                        interventions_view: dict | None) -> list[dict]:
    """Per-site [{label, applied_date, verdict}] from the interventions view.

    Verdict is taken from the 30-day horizon, falling back to 90 then 7;
    `too_early` is used only if no horizon has a firmer verdict.
    """
    if not interventions_view:
        return []
    out = []
    for row in interventions_view.get("rows", []) or []:
        if row.get("site") != key:
            continue
        horizons = row.get("horizons") or {}
        verdict = "too_early"
        for h in (30, 90, 7):
            cell = horizons.get(h) or horizons.get(str(h)) or {}
            v = cell.get("verdict")
            if v and v != "too_early":
                verdict = v
                break
        out.append({
            "label": row.get("type") or "intervention",
            "applied_date": row.get("applied_date"),
            "verdict": verdict,
        })
    return out


def _site_record(site: dict, ts_rows: list[dict], alerts: list[dict],
                 interventions: list[dict]) -> dict:
    """One compact per-site detail record for the embedded console data."""
    wpe = site.get("wpe") or {}
    cf = site.get("cf") or {}
    an = cf.get("analytics") or {}
    ts_sorted = sorted(ts_rows, key=lambda r: r.get("date") or "")
    # threat_series and threat_dates must stay index-parallel — one filter.
    threat_rows = [r for r in ts_sorted if r.get("threats") is not None]
    return {
        "key": site.get("key"),
        "safe_key": _safe_key(site.get("key") or ""),
        "account": wpe.get("account_name") or wpe.get("account")
        or "(unassigned)",
        "install": wpe.get("install"),
        "zone": cf.get("zone_id"),
        "join": site.get("join_state"),
        "apex": site.get("apex"),
        "status": _status_for_site(site.get("join_state"), alerts),
        "bandwidth_gb": wpe.get("bandwidth_gb_30d"),
        "visits": wpe.get("billable_visits_30d"),
        "mb_per_visit": wpe.get("mb_per_visit"),
        "storage_gb": wpe.get("storage_gb"),
        "cdn_gb": wpe.get("cdn_gb_30d"),
        "origin_gb": wpe.get("origin_gb_30d"),
        "cache_hit": an.get("cache_hit_rate"),
        "requests_30d": an.get("requests_30d"),
        "threats_30d": an.get("threats"),
        "pct_5xx_7d": an.get("pct_5xx_7d"),
        "requests_5xx_7d": an.get("requests_5xx_7d"),
        "requests_7d": an.get("requests_7d"),
        "cf_config": _cf_config_summary(site.get("cf"))
        if site.get("cf") else None,
        "bw_series": [r["bandwidth_gb"] for r in ts_sorted
                      if r.get("bandwidth_gb") is not None],
        "threat_series": [r["threats"] for r in threat_rows],
        "threat_dates": [r["date"] for r in threat_rows],
        "storage_series": _storage_series(wpe),
        "alerts": [{"severity": a.get("severity"), "rule": a.get("rule"),
                    "summary": a.get("summary")} for a in alerts],
        "analytics": site.get("analytics") or {"ga4": None, "gsc": None},
        "interventions": interventions,
    }


def build_console_data(snapshot: dict, timeseries_rows: list[dict],
                       interventions_view: dict | None = None) -> list[dict]:
    """One compact detail record per site in the snapshot."""
    ts_by_key: dict[str, list[dict]] = {}
    for r in timeseries_rows or []:
        ts_by_key.setdefault(r.get("key"), []).append(r)
    # Only active alerts surface in the per-site panel — resolved and muted
    # entries are stale or silenced, not things to act on.
    alerts_by_key: dict[str, list[dict]] = {}
    for a in snapshot.get("alerts", []) or []:
        if (a.get("state") or "new") in ("new", "ongoing"):
            alerts_by_key.setdefault(a.get("site_key"), []).append(a)
    out = []
    for site in snapshot.get("sites", []) or []:
        key = site.get("key")
        if not key:
            continue
        out.append(_site_record(
            site, ts_by_key.get(key, []), alerts_by_key.get(key, []),
            _site_interventions(key, interventions_view)))
    return out


def _severity_weight(rec: dict) -> int:
    """Highest alert-severity weight on a record (0 if no alerts)."""
    return max((_SEVERITY_WEIGHT.get((a.get("severity") or "").lower(), 0)
                for a in rec["alerts"]), default=0)


def _default_key(data: list[dict]) -> str | None:
    """Key of the worst site: highest alert severity, then alert count, then
    bandwidth. If no site has alerts, the highest-bandwidth WPE site. Never a
    site without WPE data unless that is all the snapshot contains."""
    if not data:
        return None
    alerted = [r for r in data if r["alerts"]]
    if alerted:
        best = max(alerted, key=lambda r: (
            _severity_weight(r), len(r["alerts"]), r.get("bandwidth_gb") or 0))
        return best["key"]
    wpe_sites = [r for r in data if r.get("bandwidth_gb") is not None]
    if wpe_sites:
        return max(wpe_sites, key=lambda r: r["bandwidth_gb"])["key"]
    return data[0]["key"]


def _ordered_groups(data: list[dict]) -> list[tuple[str, list[dict]]]:
    """Records grouped by account, ordered by total bandwidth descending,
    with the CF-only "(unassigned)" group always last."""
    groups: dict[str, list[dict]] = {}
    for r in data:
        groups.setdefault(r["account"], []).append(r)

    def sort_key(item: tuple[str, list[dict]]) -> tuple:
        name, items = item
        total_bw = sum(r.get("bandwidth_gb") or 0 for r in items)
        # name is the final tie-break so equal-bandwidth accounts are stable.
        return (name == "(unassigned)", -total_bw, name)

    return sorted(groups.items(), key=sort_key)


def _row_alert_sort_key(rec: dict) -> tuple:
    """Sort key for the default "Alerts" row order within a group:
    most severe first, then most alerts, then highest bandwidth."""
    return (-_severity_weight(rec), -len(rec["alerts"]),
            -(rec.get("bandwidth_gb") or 0))


def _row_sparkline(series: list, status: str) -> str:
    """A tiny status-coloured sparkline for a site-list row, or an empty
    placeholder span when there is too little history."""
    vals = [v for v in (series or []) if isinstance(v, (int, float))]
    if len(vals) < 2:
        return '<span class="fc-spark"></span>'
    w, h, pad = 38, 15, 2
    peak = max(vals) or 1
    n = len(vals)
    pts = " ".join(
        f"{pad + (w - 2 * pad) * (i / (n - 1)):.1f},"
        f"{pad + (h - 2 * pad) * (1 - v / peak):.1f}"
        for i, v in enumerate(vals))
    color = {"crit": "#ef4444", "warn": "#f59e0b",
             "nodata": "#6a6f78"}.get(status, "#22c55e")
    return (f'<svg class="fc-spark" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none"><polyline points="{pts}" '
            f'fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


def _site_row_html(d: dict) -> str:
    """One <li> site row for the list column."""
    bw = d.get("bandwidth_gb")
    gb = f"{bw:,.0f} GB" if isinstance(bw, (int, float)) else "—"
    n_alerts = len(d["alerts"])
    badge = f'<span class="fc-badge">{n_alerts}</span>' if n_alerts else ""
    return (
        f'<li class="fc-row" data-key="{_esc(d["key"])}" '
        f'data-bw="{bw or 0}" data-alerts="{n_alerts}" '
        f'data-sev="{_severity_weight(d)}" '
        f'onclick="selectSite(this.dataset.key)">'
        f'<span class="fc-st st-{d["status"]}"></span>'
        f'<span class="fc-dom">{_esc(d["key"])}</span>'
        f'{_row_sparkline(d.get("bw_series"), d["status"])}'
        f'<span class="fc-gb">{_esc(gb)}</span>'
        f'{badge}</li>')


def _site_list_html(data: list[dict]) -> str:
    """Account-grouped site list, default ("Alerts") row order, unassigned
    group last."""
    blocks = []
    for account, rows in _ordered_groups(data):
        rows_sorted = sorted(rows, key=_row_alert_sort_key)
        items = "".join(_site_row_html(d) for d in rows_sorted)
        blocks.append(
            f'<div class="fc-grp" onclick="toggleGroup(this)">'
            f'<span>{_esc(account)}</span>'
            f'<span class="c">{len(rows_sorted)}</span></div>'
            f'<ul class="fc-glist">{items}</ul>')
    return "".join(blocks)


def _fleet_header_html(snapshot: dict, data: list[dict]) -> str:
    """The one-line fleet summary at the top of the list column."""
    n_sites = len(data)
    # Count active alerts only — resolved/muted are not actionable.
    n_alerts = sum(1 for a in (snapshot.get("alerts") or [])
                   if (a.get("state") or "new") in ("new", "ongoing"))
    n_wpe = len({d["account"] for d in data
                 if d["account"] != "(unassigned)"})
    fresh_label, fresh_class = _freshness(snapshot.get("captured_at", ""))
    return (
        '<div class="fc-fleet">'
        f'<span><b>{n_sites:,}</b> sites</span>'
        f'<span class="red"><b>{n_alerts}</b> alerts</span>'
        f'<span><b>{n_wpe}</b> WPE</span>'
        f'<a class="fc-pill fc-pill-{fresh_class}" href="/pipeline" '
        f'title="open pipeline health">{_esc(fresh_label)}</a>'
        '</div>')


from .models import freshness as _freshness  # noqa: E402


def _sidebar_html() -> str:
    """The dark left sidebar: brand, cross-nav, user chip, refresh button."""
    nav = [("Console", "console.html", True),
           ("Overview", "dashboard.html#overview", False),
           ("Sites", "dashboard.html#sites", False),
           ("Trends", "dashboard.html#trends", False),
           ("Changelog", "dashboard.html#changelog", False),
           ("Interventions", "dashboard.html#interventions", False)]
    links = []
    for label, href, on in nav:
        cls = ' class="on"' if on else ''
        links.append(f'<a href="{href}"{cls}>{_esc(label)}</a>')
    return (
        '<aside class="fc-side">'
        '<div class="fc-brand"><span class="mk">F</span>Fleet Console</div>'
        f'<nav class="fc-nav">{"".join(links)}</nav>'
        '<div class="fc-foot">'
        '<div class="fc-user"><span class="av">R</span>'
        '<span>Roland<br><small>Admin</small></span></div>'
        '<button class="fc-refresh" onclick="refreshFleet()">'
        '&#x21BB; Refresh fleet</button>'
        '</div></aside>')


_CONSOLE_CSS = """
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font-family:"Segoe UI Variable Text",-apple-system,system-ui,sans-serif;
  font-size:13px;color:#14151a;background:#f1f2f4;
  font-feature-settings:"tnum"}
.fc{display:grid;grid-template-columns:188px 236px 1fr;height:100vh}

.fc-side{background:#0f1115;color:#c9ccd2;display:flex;flex-direction:column;
  padding:16px 12px}
.fc-brand{display:flex;align-items:center;gap:8px;font-weight:700;color:#fff;
  font-size:14px;margin-bottom:18px}
.fc-brand .mk{width:26px;height:26px;border-radius:7px;background:#c8f250;
  color:#0f1115;display:flex;align-items:center;justify-content:center;
  font-weight:800}
.fc-nav a{display:block;padding:8px 10px;border-radius:8px;color:#c9ccd2;
  text-decoration:none;font-size:12.5px;margin-bottom:1px}
.fc-nav a:hover{background:#1d2027;color:#fff}
.fc-nav a.on{background:#c8f250;color:#0f1115;font-weight:700}
.fc-foot{margin-top:auto;display:flex;flex-direction:column;gap:9px}
.fc-user{display:flex;align-items:center;gap:8px;font-size:11.5px}
.fc-user .av{width:28px;height:28px;border-radius:50%;background:#2a2d36;
  color:#fff;display:flex;align-items:center;justify-content:center;
  font-weight:700}
.fc-user small{color:#6a6f78}
.fc-refresh{background:#c8f250;color:#0f1115;border:0;border-radius:999px;
  padding:8px;font:inherit;font-weight:700;font-size:12px;cursor:pointer}
.fc-refresh:hover{background:#b3df3f}

.fc-list{background:#0f1115;border-right:1px solid #20232b;display:flex;
  flex-direction:column;overflow:hidden}
.fc-lhead{padding:12px 12px 9px;border-bottom:1px solid #20232b}
.fc-fleet{display:flex;gap:10px;font-size:10px;color:#8b8f99;
  margin-bottom:8px;text-transform:uppercase;letter-spacing:.04em;
  align-items:center;flex-wrap:wrap}
.fc-fleet b{color:#c9ccd2}
.fc-fleet .red b{color:#f87171}
.fc-pill{margin-left:auto;font-size:9.5px;font-weight:700;padding:2px 8px;
  border-radius:999px;letter-spacing:.05em;text-transform:none;
  text-decoration:none;cursor:pointer}
.fc-pill:hover{filter:brightness(1.1)}
.fc-pill-fresh{background:#14532d;color:#86efac}
.fc-pill-aging{background:#78350f;color:#fcd34d}
.fc-pill-stale{background:#7f1d1d;color:#fca5a5}
.fc-search{width:100%;background:#1b1e26;border:1px solid #2a2d36;
  border-radius:7px;padding:7px 9px;color:#c9ccd2;font:inherit;font-size:12px}
.fc-search::placeholder{color:#6a6f78}
.fc-sort{display:flex;gap:4px;margin-top:7px}
.fc-sort button{flex:1;font:inherit;font-size:10.5px;padding:4px 6px;border:0;
  border-radius:5px;background:transparent;color:#8b8f99;cursor:pointer}
.fc-sort button.on{background:#2a2d36;color:#e6e8ec}
.fc-rows{overflow-y:auto;flex:1;padding:6px}
.fc-grp{font-size:10px;text-transform:uppercase;letter-spacing:.05em;
  color:#6a6f78;padding:10px 8px 4px;display:flex;
  justify-content:space-between;cursor:pointer}
.fc-grp .c{background:#1b1e26;border-radius:999px;padding:1px 7px}
.fc-grp.collapsed + .fc-glist{display:none}
.fc-glist{list-style:none;margin:0;padding:0}
.fc-row{display:flex;align-items:center;gap:8px;padding:7px 8px;
  border-radius:7px;cursor:pointer}
.fc-row:hover{background:#1b1e26}
.fc-row.sel{background:#1d2027;box-shadow:inset 2px 0 0 #c8f250}
.fc-row.hidden{display:none}
.fc-st{width:7px;height:7px;border-radius:50%;flex:0 0 7px}
.st-crit{background:#ef4444}.st-warn{background:#f59e0b}
.st-ok{background:#22c55e}.st-nodata{background:#4b5563}
.fc-dom{flex:1;color:#c9ccd2;font-size:12px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.fc-row.sel .fc-dom{color:#fff;font-weight:600}
.fc-spark{width:38px;height:15px;flex:0 0 38px}
.fc-gb{font-size:11px;color:#8b8f99;font-variant-numeric:tabular-nums;
  min-width:42px;text-align:right}
.fc-badge{background:#7f1d1d;color:#fecaca;font-size:9.5px;font-weight:700;
  border-radius:999px;padding:1px 6px}

.fc-panel{background:#f1f2f4;overflow-y:auto;padding:18px 20px}
.fc-empty{color:#9aa0aa;padding:48px;text-align:center}
.fc-phead{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.fc-ptitle{font-size:18px;font-weight:700;margin:0}
.pill{padding:2px 9px;border-radius:999px;font-size:10px;font-weight:700;
  text-transform:uppercase}
.pill-join{background:#e6edff;color:#2563eb}
.pill-alert{background:#fde2e2;color:#c0392b}
.fc-full{margin-left:auto;font-size:11.5px;color:#2563eb;
  text-decoration:none}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;
  margin-bottom:9px}
.kpi{background:#fff;border:1px solid #e5e6ea;border-radius:10px;
  padding:10px 11px}
.kpi .kl{font-size:9.5px;text-transform:uppercase;letter-spacing:.04em;
  color:#8b8f99}
.kpi .kv{font-size:18px;font-weight:700;margin-top:3px;
  font-variant-numeric:tabular-nums}
.kpi .kv.flag{color:#dc2626}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-bottom:9px}
.cd{background:#fff;border:1px solid #e5e6ea;border-radius:10px;
  padding:12px 13px}
.cd h4{margin:0 0 9px;font-size:10.5px;text-transform:uppercase;
  letter-spacing:.04em;color:#8b8f99}
.cd-sub{font-size:10.5px;color:#8b8f99;margin-top:6px}
.dl{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:12px;
  margin:0}
.dl dt{color:#6a6f78}
.dl dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}
.dl dd.good{color:#15803d;font-weight:600}
.dl dd.warnv{color:#b9770e;font-weight:600}
.an-cols{display:grid;grid-template-columns:1fr 1fr;gap:18px}
@media (max-width:680px){.an-cols{grid-template-columns:1fr}}
.an-sub{display:flex;flex-direction:column;gap:7px}
.an-head{display:flex;align-items:baseline;justify-content:space-between;
  gap:8px;border-bottom:1px solid #e5e6ea;padding-bottom:4px}
.an-head h3{margin:0;font-size:10px;font-weight:600;text-transform:uppercase;
  letter-spacing:.06em;color:#6a6f78}
.an-src{font-size:9.5px;color:#9aa0aa;font-style:italic}
.an-rows{display:grid;grid-template-columns:auto 1fr auto;column-gap:12px;
  row-gap:5px;font-size:12px;align-items:baseline}
.an-k{color:#6a6f78}
.an-v{font-weight:600;font-variant-numeric:tabular-nums;text-align:right}
.an-d{font-size:11px;font-variant-numeric:tabular-nums;color:#9aa0aa;
  white-space:nowrap;text-align:right;justify-self:end}
.an-d.an-down{color:#b91c1c;font-weight:600}
.an-d.an-up{color:#047857;font-weight:600}
.an-note{color:#9aa0aa;font-weight:400;margin-left:3px}
.an-empty{font-size:12px;margin:4px 0 0}
.chartbox{width:100%;height:74px;display:block}
.muted{color:#9aa0aa;font-size:12px}
.alert{font-size:11.5px;padding:6px 0;border-bottom:1px solid #f0f1f3;
  display:flex;gap:6px;align-items:center}
.alert:last-child{border:0}
.sev{font-size:9px;font-weight:700;padding:1px 6px;border-radius:999px;
  text-transform:uppercase}
.sev-critical{background:#fde2e2;color:#c0392b}
.sev-warning{background:#fef0d9;color:#b9770e}
.sev-info{background:#e6edff;color:#2563eb}
.ivrow{font-size:11.5px;padding:6px 0;border-bottom:1px solid #f0f1f3;
  display:flex;justify-content:space-between;gap:8px}
.ivrow:last-child{border:0}
.verdict{font-size:9.5px;font-weight:700;padding:1px 7px;border-radius:999px;
  white-space:nowrap}
.v-worked{background:#dcfce7;color:#15803d}
.v-regressed{background:#fde2e2;color:#c0392b}
.v-no_effect,.v-too_early,.v-baseline_unavailable{background:#f1f2f4;
  color:#6a6f78}
.deep{background:#fff;border:1px dashed #cfd2d8;border-radius:10px;
  padding:11px 13px;font-size:11.5px;color:#6a6f78;display:flex;
  align-items:center;gap:8px}
.deep a{color:#2563eb;text-decoration:none;font-weight:600;margin-left:auto}
"""


_CONSOLE_JS = """
var CONSOLE = JSON.parse(document.getElementById('console-data').textContent);
var DEFAULT_KEY = JSON.parse(
  document.getElementById('default-key').textContent);
var BY_KEY = {};
CONSOLE.forEach(function(d){ BY_KEY[d.key] = d; });

function _esc(s){
  return String(s==null?'':s).replace(/&/g,'&amp;')
    .replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function _fmt(v, suffix){
  if(v===null||v===undefined) return '—';
  var n = (typeof v==='number')
    ? v.toLocaleString(undefined,{maximumFractionDigits:1}) : v;
  return n + (suffix||'');
}
function _line(vals, color){
  if(!vals || vals.length<2)
    return '<div class="muted">history building</div>';
  var W=300,H=74,P=6, pk=Math.max.apply(null,vals)||1, n=vals.length;
  var pts = vals.map(function(v,i){
    var x=P+(W-2*P)*(i/(n-1)), y=P+(H-2*P)*(1-(v/pk));
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  return '<svg class="chartbox" viewBox="0 0 '+W+' '+H+'" '+
    'preserveAspectRatio="none"><polyline points="'+pts+'" fill="none" '+
    'stroke="'+color+'" stroke-width="2.5" stroke-linejoin="round" '+
    'stroke-linecap="round"/></svg>';
}
function _twoLine(a, b, ca, cb){
  var aOk=a&&a.length>=2, bOk=b&&b.length>=2;
  if(!aOk && !bOk) return '<div class="muted">history building</div>';
  var W=300,H=74,P=6;
  var pk=Math.max.apply(null,(a||[]).concat(b||[]))||1;
  function path(vals,color){
    if(!vals||vals.length<2) return '';
    var n=vals.length;
    var pts=vals.map(function(v,i){
      var x=P+(W-2*P)*(i/(n-1)), y=P+(H-2*P)*(1-(v/pk));
      return x.toFixed(1)+','+y.toFixed(1);
    }).join(' ');
    return '<polyline points="'+pts+'" fill="none" stroke="'+color+'" '+
      'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>';
  }
  return '<svg class="chartbox" viewBox="0 0 '+W+' '+H+'" '+
    'preserveAspectRatio="none">'+path(a,ca)+path(b,cb)+'</svg>';
}
function _peakDate(series, dates){
  if(!series || !series.length) return null;
  var mi=0;
  for(var i=1;i<series.length;i++){ if(series[i]>series[mi]) mi=i; }
  return (dates && dates[mi]) ? dates[mi] : null;
}

function toggleGroup(h){ h.classList.toggle('collapsed'); }

function filterSites(){
  var q=document.getElementById('fc-search').value.toLowerCase();
  document.querySelectorAll('.fc-row').forEach(function(r){
    r.classList.toggle('hidden', r.dataset.key.toLowerCase().indexOf(q)<0);
  });
}

function setSort(mode, btn){
  document.querySelectorAll('.fc-sort button').forEach(function(b){
    b.classList.remove('on'); });
  btn.classList.add('on');
  document.querySelectorAll('.fc-glist').forEach(function(list){
    var rows=[].slice.call(list.children);
    rows.sort(function(x,y){
      if(mode==='name') return x.dataset.key<y.dataset.key?-1:1;
      if(mode==='bandwidth') return (+y.dataset.bw)-(+x.dataset.bw);
      var d=(+y.dataset.sev)-(+x.dataset.sev);
      if(d) return d;
      d=(+y.dataset.alerts)-(+x.dataset.alerts);
      if(d) return d;
      return (+y.dataset.bw)-(+x.dataset.bw);
    });
    rows.forEach(function(r){ list.appendChild(r); });
  });
}

function _cfConfigHtml(d){
  var c=d.cf_config;
  if(!c) return '<div class="muted">No Cloudflare zone for this site.</div>';
  var tlsWarn = (c.tls && parseFloat(c.tls)<1.2);
  // CF returns "block" when AI-bot protection is on, "disabled" when off.
  var aiCls = !c.ai_protection ? ''
            : (c.ai_protection === 'disabled' ? 'warnv' : 'good');
  var proxy = (c.dns_proxy_www===null||c.dns_proxy_www===undefined)
    ? '—' : (c.dns_proxy_www?'yes':'no');
  return '<dl class="dl">'+
    '<dt>Zone</dt><dd>'+_esc(String(d.zone||'—').slice(0,16))+'</dd>'+
    '<dt>Min TLS</dt><dd class="'+(tlsWarn?'warnv':'')+'">'+
      _esc(_fmt(c.tls))+'</dd>'+
    '<dt>SSL mode</dt><dd>'+_esc(_fmt(c.ssl))+'</dd>'+
    '<dt>WAF rules</dt><dd>'+_fmt(c.waf_count)+'</dd>'+
    '<dt>Cache rules</dt><dd>'+_fmt(c.cache_rule_count)+'</dd>'+
    '<dt>AI scrapers</dt><dd class="'+aiCls+'">'+
      (c.ai_protection?_esc(c.ai_protection):'—')+'</dd>'+
    '<dt>www proxied</dt><dd>'+proxy+'</dd>'+
    '</dl>';
}
function _analyticsHtml(d){
  var a = d.analytics || {};
  var g = a.ga4, s = a.gsc;
  function trend(now, prev){
    if(now==null || prev==null || prev===0) return '<span class="an-d"></span>';
    var pct = (now - prev) / prev * 100;
    if(pct < 0) return '<span class="an-d an-down">↓ '+
      Math.abs(pct).toFixed(0)+'%<span class="an-note"> vs prior week</span></span>';
    if(pct > 0) return '<span class="an-d an-up">↑ '+
      pct.toFixed(0)+'%<span class="an-note"> vs prior week</span></span>';
    return '<span class="an-d an-note">no change</span>';
  }
  function row(k, v, t){
    return '<span class="an-k">'+k+'</span>'+
      '<span class="an-v">'+v+'</span>'+
      (t || '<span class="an-d"></span>');
  }
  function sub(title, src, body){
    var head = '<div class="an-head"><h3>'+title+'</h3>'+
      (src ? '<span class="an-src" title="mapping source">'+
        _esc(src)+'-mapped</span>' : '')+'</div>';
    return '<div class="an-sub">'+head+body+'</div>';
  }
  function emptySub(title, msg){
    return '<div class="an-sub"><div class="an-head"><h3>'+title+
      '</h3></div><p class="muted an-empty">'+msg+'</p></div>';
  }
  var gHtml = g
    ? sub('Google Analytics', g.source || '',
        '<div class="an-rows">'+
          row('Sessions', _fmt(g.sessions_30d),
              trend(g.sessions_7d, g.sessions_prev_7d))+
          row('Conversions', _fmt(g.conversions_30d),
              trend(g.conversions_7d, g.conversions_prev_7d))+
          row('Engagement', (g.engagement_rate==null ? '—' :
              (g.engagement_rate*100).toFixed(0)+'%'))+
        '</div>')
    : emptySub('Google Analytics', 'No GA4 access for this site.');
  var sHtml = s
    ? sub('Google Search Console', s.source || '',
        '<div class="an-rows">'+
          row('Clicks', _fmt(s.clicks_30d),
              trend(s.clicks_7d, s.clicks_prev_7d))+
          row('Impressions', _fmt(s.impressions_30d))+
        '</div>')
    : emptySub('Google Search Console', 'No GSC access for this site.');
  return '<div class="an-cols">'+gHtml+sHtml+'</div>';
}

function selectSite(key){
  var d=BY_KEY[key];
  if(!d) return;
  document.querySelectorAll('.fc-row').forEach(function(r){
    r.classList.toggle('sel', r.dataset.key===key);
  });
  var mbFlag = (typeof d.mb_per_visit==='number' && d.mb_per_visit>30);
  var alertPill = d.alerts.length
    ? '<span class="pill pill-alert">'+d.alerts.length+' alert'+
      (d.alerts.length>1?'s':'')+'</span>' : '';
  var alertsHtml = d.alerts.length
    ? d.alerts.map(function(a){
        return '<div class="alert"><span class="sev sev-'+
          _esc(a.severity)+'">'+_esc(a.severity)+'</span><strong>'+
          _esc(a.rule)+'</strong>'+
          (a.summary?' &middot; '+_esc(a.summary):'')+'</div>';
      }).join('')
    : '<div class="muted">All clear &mdash; no active alerts.</div>';
  var peak=_peakDate(d.threat_series,d.threat_dates);
  var ivHtml = d.interventions.length
    ? d.interventions.map(function(v){
        return '<div class="ivrow"><span>'+_esc(v.label)+
          (v.applied_date?' &middot; '+_esc(v.applied_date):'')+
          '</span><span class="verdict v-'+_esc(v.verdict)+'">'+
          _esc(String(v.verdict).replace(/_/g,' '))+'</span></div>';
      }).join('')
    : '<div class="muted">No recorded interventions.</div>';
  var stg=d.storage_series||[];
  var fileS=stg.map(function(p){ return p.file_gb; });
  var dbS=stg.map(function(p){ return p.db_gb; });
  var stgLegend = stg.length
    ? '<div class="cd-sub">&#9632; files '+
      _fmt(stg[stg.length-1].file_gb,' GB')+
      ' &nbsp; &#9632; database '+_fmt(stg[stg.length-1].db_gb,' GB')+
      '</div>' : '';
  var bwSub = (d.cdn_gb!=null && d.origin_gb!=null)
    ? '<div class="cd-sub">CDN '+_fmt(d.cdn_gb,' GB')+
      ' &middot; origin '+_fmt(d.origin_gb,' GB')+'</div>' : '';

  document.getElementById('fc-panel').innerHTML =
    '<div class="fc-phead"><h2 class="fc-ptitle">'+_esc(d.key)+'</h2>'+
      '<span class="pill pill-join">'+_esc(d.join||'?')+'</span>'+
      alertPill+
      '<a class="fc-full" href="sites/'+_esc(d.safe_key)+
      '.html">full per-site page &rarr;</a></div>'+
    '<div class="kpis">'+
      '<div class="kpi"><div class="kl">Bandwidth 30d</div>'+
        '<div class="kv">'+_fmt(d.bandwidth_gb,' GB')+'</div></div>'+
      '<div class="kpi"><div class="kl">Visits 30d</div>'+
        '<div class="kv">'+_fmt(d.visits)+'</div></div>'+
      '<div class="kpi"><div class="kl">MB / visit</div>'+
        '<div class="kv'+(mbFlag?' flag':'')+'">'+
        _fmt(d.mb_per_visit)+'</div></div>'+
      '<div class="kpi"><div class="kl">Storage</div>'+
        '<div class="kv">'+_fmt(d.storage_gb,' GB')+'</div></div>'+
      '<div class="kpi"><div class="kl">Cache hit</div>'+
        '<div class="kv">'+
        (d.cache_hit==null?'—':_fmt(d.cache_hit,'%'))+
        '</div></div>'+
    '</div>'+
    '<div class="grid2">'+
      '<div class="cd"><h4>Bandwidth trend</h4>'+
        _line(d.bw_series,'#2563eb')+bwSub+'</div>'+
      '<div class="cd"><h4>Threats trend</h4>'+
        _line(d.threat_series,'#dc2626')+'</div>'+
    '</div>'+
    '<div class="grid2">'+
      '<div class="cd"><h4>Cloudflare config</h4>'+_cfConfigHtml(d)+'</div>'+
      '<div class="cd"><h4>Security &amp; alerts</h4>'+alertsHtml+
        '<div class="alert"><span style="color:#6a6f78">Threats 30d</span>'+
        '<span style="margin-left:auto;font-weight:600">'+
        _fmt(d.threats_30d)+'</span></div>'+
        (peak?'<div class="alert">'+
          '<span style="color:#6a6f78">Peak threat day</span>'+
          '<span style="margin-left:auto">'+_esc(peak)+'</span></div>':'')+
        (d.pct_5xx_7d!=null?'<div class="alert'+
          ((d.pct_5xx_7d>=3)?' crit':(d.pct_5xx_7d>=1?' warn':''))+'">'+
          '<span style="color:#6a6f78">5xx 7d</span>'+
          '<span style="margin-left:auto;font-weight:600">'+
          _fmt(d.pct_5xx_7d,'%')+
          ' <span style="color:#6a6f78;font-weight:400">('+
          _fmt(d.requests_5xx_7d)+'/'+_fmt(d.requests_7d)+')</span>'+
          '</span></div>':'')+
        '</div>'+
    '</div>'+
    '<div class="cd" style="margin-bottom:9px"><h4>Analytics (30d)</h4>'+
      _analyticsHtml(d)+'</div>'+
    '<div class="grid2">'+
      '<div class="cd"><h4>Storage trend</h4>'+
        _twoLine(fileS,dbS,'#7c3aed','#9aa0aa')+stgLegend+'</div>'+
      '<div class="cd"><h4>Intervention history</h4>'+ivHtml+'</div>'+
    '</div>'+
    '<div class="deep"><span>&#127758; Traffic geography &middot; '+
      'top paths &middot; top user-agents &mdash; fetched per-site '+
      'on demand</span><a href="sites/'+_esc(d.safe_key)+
      '.html">open full per-site page &rarr;</a></div>';
}

function refreshFleet(){
  if(location.protocol==='file:'){
    alert('Refresh only works when served via http://localhost:8765/.');
    return;
  }
  fetch('/refresh',{method:'POST'});
  alert('Pipeline refresh started. Reload in ~10-13 min.');
}

if(DEFAULT_KEY) selectSite(DEFAULT_KEY);
"""


def render_console(snapshot: dict, timeseries_rows: list[dict],
                   interventions_view: dict | None = None) -> str:
    """Return the complete self-contained Fleet Console HTML string."""
    data = build_console_data(snapshot, timeseries_rows, interventions_view)
    list_html = (_site_list_html(data) if data
                 else '<div class="muted">No sites in the latest '
                      'snapshot.</div>')
    # Harden the embedded JSON against a `</script>` breakout (json.dumps
    # does not escape `/`).
    data_json = json.dumps(data, default=str).replace("</", "<\\/")
    default_json = json.dumps(_default_key(data)).replace("</", "<\\/")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fleet Console &middot; {_esc(snapshot.get("date", "?"))}</title>
<style>{_CONSOLE_CSS}</style></head><body>
<div class="fc">
{_sidebar_html()}
<div class="fc-list">
  <div class="fc-lhead">
    {_fleet_header_html(snapshot, data)}
    <input id="fc-search" class="fc-search" type="search"
           placeholder="Search sites..." oninput="filterSites()">
    <div class="fc-sort">
      <button class="on" onclick="setSort('alerts',this)">Alerts</button>
      <button onclick="setSort('bandwidth',this)">Bandwidth</button>
      <button onclick="setSort('name',this)">Name</button>
    </div>
  </div>
  <div class="fc-rows">{list_html}</div>
</div>
<main class="fc-panel" id="fc-panel">
  <div class="fc-empty">Select a site from the list.</div>
</main>
</div>
<script id="console-data" type="application/json">{data_json}</script>
<script id="default-key" type="application/json">{default_json}</script>
<script>{_CONSOLE_JS}</script>
</body></html>"""
