"""Per-site drill-down page rendering. One HTML file per site under data/sites/."""
from __future__ import annotations
import re
import html as html_mod
from .models import SITES_DIR

_UNSAFE = re.compile(r"[^a-z0-9.\-]")


def safe_key(key: str) -> str:
    """Filename-safe version of a site key (domain). Lowercase, [a-z0-9.-] only."""
    return _UNSAFE.sub("-", key.lower())


def _esc(v) -> str:
    return html_mod.escape(str(v))


def _stats_card(label: str, value: str, sub: str = "",
                health: str = "", status_word: str = "") -> str:
    """One stat card. `health` is "", "ok", "watch", or "bad" — drives the
    value's colour. `status_word` is a short colourblind-safe label
    ("ok"/"watch"/"bad") rendered as a pill next to the subtitle."""
    sub_html = f'<span class="ss-sub muted">{_esc(sub)}</span>' if sub else ""
    pill_html = (f'<span class="ss-pill ss-pill-{health}">{_esc(status_word)}</span>'
                 if health and status_word else "")
    card_cls = f"ss-card ss-card-{health}" if health else "ss-card"
    value_cls = f"ss-value ss-value-{health}" if health else "ss-value"
    return (f'<div class="{card_cls}">'
            f'<span class="ss-label muted">{_esc(label)}</span>'
            f'<span class="{value_cls}">{_esc(value)}</span>'
            f'{sub_html}{pill_html}</div>')


# Map each stat card label to the rule(s) that govern its health. Labels
# absent from this map stay neutral grey (no monitoring exists for them).
_CARD_RULE_MAP: dict[str, tuple[str, ...]] = {
    "Bandwidth 30d": ("bandwidth_spike",),
    "MB / visit": ("mb_per_visit_high",),
    "Cache hit rate": ("cache_hit_low",),
    "Threats 30d": ("threat_spike",),
    "5xx rate 7d": ("edge_5xx_rate",),
    "Cert expiry": ("cert_expiry",),
}

_SEV_RANK = {"critical": 2, "warning": 1, "info": 0}


def _alerts_by_rule(snapshot: dict, site_key: str) -> dict[str, str]:
    """{rule_id: worst-severity} for active alerts on a site. Resolved/muted skipped."""
    out: dict[str, str] = {}
    for a in (snapshot.get("alerts") or []):
        if a.get("site_key") != site_key:
            continue
        if (a.get("state") or "new") in ("resolved", "muted"):
            continue
        rule = a.get("rule")
        sev = a.get("severity")
        if rule and _SEV_RANK.get(sev, -1) > _SEV_RANK.get(out.get(rule), -1):
            out[rule] = sev
    return out


def _card_health(label: str, by_rule: dict[str, str]) -> tuple[str, str]:
    """Return (health_class, status_word) for the stat card.

    health_class: "" neutral / "ok" green / "watch" amber / "bad" red.
    A card whose rule(s) are registered but not firing for this site reads
    "ok" — actively monitored AND clean is signal worth showing.
    """
    rule_ids = _CARD_RULE_MAP.get(label)
    if not rule_ids:
        return ("", "")
    worst = None
    for rid in rule_ids:
        sev = by_rule.get(rid)
        if sev and _SEV_RANK.get(sev, -1) > _SEV_RANK.get(worst, -1):
            worst = sev
    if worst == "critical":
        return ("bad", "bad")
    if worst == "warning":
        return ("watch", "watch")
    return ("ok", "ok")


def _header(site: dict) -> str:
    return f'''
    <header class="sp-head">
      <div class="sp-head-left">
        <a href="../dashboard.html" class="sp-back">&larr; Back to fleet</a>
        <h1 class="sp-title">{_esc(site["key"])}</h1>
        <span class="join-pill join-{_esc(site.get("join_state","unknown"))}">{_esc(site.get("join_state","unknown"))}</span>
      </div>
      <button id="sp-refresh" class="sp-refresh-btn"
              onclick="refreshSite('{_esc(site["key"])}')">&#x27F3; Refresh this site</button>
    </header>'''


def _quick_stats(site: dict, snapshot: dict | None = None) -> str:
    wpe = site.get("wpe") or {}
    cf = site.get("cf") or {}
    an = cf.get("analytics") or {}
    by_rule = (_alerts_by_rule(snapshot, site.get("key", ""))
               if snapshot else {})

    def _card(label: str, value: str, sub: str = "") -> str:
        h, w = _card_health(label, by_rule)
        return _stats_card(label, value, sub, health=h, status_word=w)

    cards = []
    if wpe:
        bw = wpe.get("bandwidth_gb_30d")
        cards.append(_card("Bandwidth 30d",
                           f"{bw:,.1f} GB" if isinstance(bw, (int, float)) else "—",
                           f"account {wpe.get('account_name','?')}"))
        v = wpe.get("billable_visits_30d")
        cards.append(_card("Billable visits 30d",
                           f"{v:,}" if isinstance(v, (int, float)) else "—"))
        mbv = wpe.get("mb_per_visit")
        cards.append(_card("MB / visit",
                           f"{mbv:.1f}" if isinstance(mbv, (int, float)) else "—"))
    if cf:
        plan = cf.get("plan") or {}
        if plan.get("name"):
            price = plan.get("price") or 0
            freq = plan.get("frequency") or "monthly"
            cur = plan.get("currency") or "USD"
            value = (f"{plan['name']}" if not price
                     else f"{plan['name']}")
            sub = ("free" if not price
                   else f"{cur} {price:g}/{freq[:2]}")
            cards.append(_card("CF plan", value, sub))
        chr_ = an.get("cache_hit_rate")
        cards.append(_card("Cache hit rate",
                           f"{chr_:.1f}%" if isinstance(chr_, (int, float)) else "—"))
        cards.append(_card("Threats 30d",
                           f"{an.get('threats', 0):,}"))
        pct5 = an.get("pct_5xx_7d")
        if isinstance(pct5, (int, float)):
            err = an.get("requests_5xx_7d") or 0
            req = an.get("requests_7d") or 0
            cards.append(_card("5xx rate 7d", f"{pct5:.2f}%",
                               f"{err:,}/{req:,} requests"))
        ce = cf.get("cert_expiry") or {}
        days = ce.get("min_days_until_expiry")
        if isinstance(days, int):
            if days <= 0:
                value = f"EXPIRED {abs(days)}d ago"
            else:
                value = f"in {days} days"
            sub = (f'expires {ce.get("earliest_expires_on","?")} '
                   f'({ce.get("earliest_issuer","?")})')
            cards.append(_card("Cert expiry", value, sub))
    return f'<div class="ss-grid">{"".join(cards)}</div>'


def _analytics_card(site: dict) -> str:
    """Per-site GA4 + GSC summary.

    Layout: two sub-cards (GA4, GSC) in a responsive 2-col grid. Each row is
    `metric · value · trend`, where the trend reads as `↓ 23% vs prior week`
    in red/green (no "wow" jargon). Mapping source moves out of the data area
    into a small caption next to the sub-card title.
    """
    a = site.get("analytics") or {}
    ga4 = a.get("ga4")
    gsc = a.get("gsc")

    def _trend(now, prev):
        if now is None or prev is None or prev == 0:
            return '<span class="an-d"></span>'
        pct = (now - prev) / prev * 100
        if pct < 0:
            return (f'<span class="an-d an-down">&#x2193; {abs(pct):.0f}%'
                    f'<span class="an-note"> vs prior week</span></span>')
        if pct > 0:
            return (f'<span class="an-d an-up">&#x2191; {pct:.0f}%'
                    f'<span class="an-note"> vs prior week</span></span>')
        return '<span class="an-d an-note">no change</span>'

    empty_trend = '<span class="an-d"></span>'

    def _row(label, value, trend_html=''):
        # The empty span keeps the row on the 3-col grid; without it, the
        # next row's metric name lands in this row's 3rd column.
        return (f'<span class="an-k">{label}</span>'
                f'<span class="an-v">{value}</span>'
                f'{trend_html or empty_trend}')

    def _sub(title, src, rows_html):
        src_html = (f'<span class="an-src" title="mapping source">'
                    f'{_esc(src)}-mapped</span>') if src else ''
        return ('<div class="an-sub">'
                f'<div class="an-head"><h3>{title}</h3>{src_html}</div>'
                f'<div class="an-rows">{rows_html}</div></div>')

    def _empty_sub(title, msg):
        return ('<div class="an-sub">'
                f'<div class="an-head"><h3>{title}</h3></div>'
                f'<p class="muted an-empty">{msg}</p></div>')

    if ga4:
        ga4_html = _sub('Google Analytics', ga4.get("source") or "",
            _row('Sessions', f'{ga4.get("sessions_30d", 0):,}',
                 _trend(ga4.get("sessions_7d"), ga4.get("sessions_prev_7d"))) +
            _row('Conversions', f'{ga4.get("conversions_30d", 0):,}',
                 _trend(ga4.get("conversions_7d"),
                        ga4.get("conversions_prev_7d"))) +
            _row('Engagement', f'{(ga4.get("engagement_rate") or 0)*100:.0f}%'))
    else:
        ga4_html = _empty_sub('Google Analytics',
                              'No GA4 access for this site.')

    if gsc:
        gsc_html = _sub('Google Search Console', gsc.get("source") or "",
            _row('Clicks', f'{gsc.get("clicks_30d", 0):,}',
                 _trend(gsc.get("clicks_7d"), gsc.get("clicks_prev_7d"))) +
            _row('Impressions', f'{gsc.get("impressions_30d", 0):,}'))
    else:
        gsc_html = _empty_sub('Google Search Console',
                              'No GSC access for this site.')

    return ('<section class="sp-panel"><h2>Analytics (30d)</h2>'
            f'<div class="an-cols">{ga4_html}{gsc_html}</div>'
            '</section>')


def _active_alerts(site_key: str, snapshot: dict) -> str:
    # Only active alerts — resolved and muted entries are stale or silenced.
    rows = [a for a in snapshot.get("alerts", [])
            if a.get("site_key") == site_key
            and (a.get("state") or "new") in ("new", "ongoing")]
    if not rows:
        return ('<section class="sp-panel"><h2>Active alerts</h2>'
                '<p class="muted">No active alerts for this site.</p></section>')
    items = []
    for a in rows:
        items.append(
            f'<li class="alert-row sev-{_esc(a["severity"])}">'
            f'<span class="sev-pill sev-{_esc(a["severity"])}">{_esc(a["severity"])}</span> '
            f'<strong>{_esc(a["rule"])}</strong> &middot; {_esc(a.get("summary",""))}</li>')
    return (f'<section class="sp-panel"><h2>Active alerts</h2>'
            f'<ul class="alert-list">{"".join(items)}</ul></section>')


def _notice(text: str) -> str:
    return f'<div class="sp-notice muted">{_esc(text)}</div>'


def _proxy_label(v) -> str:
    if v is True: return "PROXIED (orange)"
    if v is False: return "DNS only (grey)"
    return "—"


def _cf_config_section(cf: dict) -> str:
    cfg = cf.get("config") or {}
    settings = cfg.get("settings") or {}
    bot = cfg.get("bot") or {}
    waf = cfg.get("waf_rules") or []
    cache = cfg.get("cache_rules") or []
    rows = [
        ("SSL mode", str(settings.get("ssl") or "—")),
        ("Always use HTTPS", str(settings.get("always_use_https") or "—")),
        ("Security level", str(settings.get("security_level") or "—")),
        ("Bot Fight Mode", "on" if bot.get("fight_mode") else "off"),
        ("AI bot protection", str(bot.get("ai_bots_protection") or "—")),
        ("WAF rules", str(len(waf))),
        ("Cache rules", str(len(cache))),
        ("Apex proxy", _proxy_label(cfg.get("dns_proxy_apex"))),
        ("WWW proxy", _proxy_label(cfg.get("dns_proxy_www"))),
    ]
    items = "".join(
        f'<dt class="muted">{_esc(k)}</dt><dd>{_esc(v)}</dd>'
        for k, v in rows)
    return (f'<section class="sp-panel"><h2>CF Configuration</h2>'
            f'<dl class="cf-dl">{items}</dl></section>')


def _compact(v: float) -> str:
    """Short axis-tick label: 0.8 / 9.4 / 58 / 24k / 1.3M.

    Values below 10 keep one decimal so small-magnitude charts (e.g. storage
    in GB, which is often ~1 GB) still get a readable Y-axis scale.
    """
    v = float(v)
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{v / 1_000:.0f}k"
    if a >= 10:
        return f"{v:.0f}"
    return f"{v:.1f}"


def _svg_line_chart(lines: list[dict], x_labels: list[str],
                    value_fmt, aria: str) -> str:
    """Multi-line SVG with 0-baselined Y gridlines, axis labels, and point dots.

    `lines`: [{"color": "#hex", "points": [float, ...]}, ...] — all the same
             length as `x_labels`. All lines share one Y scale (0 -> peak).
    `value_fmt`: callable(float) -> str, used for the hover-tooltip on each dot
             (full precision). Y-axis ticks always use the compact format.
    """
    n = len(x_labels)
    W, H = 760, 200
    PAD_L, PAD_R, PAD_T, PAD_B = 56, 16, 14, 30
    peak = max((max(ln["points"]) for ln in lines if ln["points"]), default=0) or 1

    def px(i): return PAD_L + (W - PAD_L - PAD_R) * (i / max(n - 1, 1))
    def py(v): return PAD_T + (H - PAD_T - PAD_B) * (1 - v / peak)

    grid = []
    for frac in (0, 0.25, 0.5, 0.75, 1):
        gy = PAD_T + (H - PAD_T - PAD_B) * (1 - frac)
        grid.append(f'<line x1="{PAD_L}" y1="{gy:.1f}" x2="{W - PAD_R}" '
                    f'y2="{gy:.1f}" class="sp-grid"/>')
        grid.append(f'<text x="{PAD_L - 7}" y="{gy + 3.5:.1f}" class="sp-axis" '
                    f'text-anchor="end">{_esc(_compact(peak * frac))}</text>')

    step = max(1, n // 4)
    xlab = []
    for i in sorted(set(list(range(0, n, step)) + [n - 1])):
        label = x_labels[i][5:] if len(x_labels[i]) > 5 else x_labels[i]
        xlab.append(f'<text x="{px(i):.1f}" y="{H - 9}" class="sp-axis" '
                    f'text-anchor="middle">{_esc(label)}</text>')

    paths, dots = [], []
    for ln in lines:
        pts = ln["points"]
        d = " ".join(("M" if i == 0 else "L") + f" {px(i):.1f} {py(v):.1f}"
                     for i, v in enumerate(pts))
        paths.append(f'<path d="{d}" fill="none" stroke="{ln["color"]}" '
                     f'stroke-width="2" stroke-linecap="round" '
                     f'stroke-linejoin="round"/>')
        for i, v in enumerate(pts):
            dots.append(
                f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="2.4" '
                f'fill="{ln["color"]}"><title>{_esc(x_labels[i])}: '
                f'{_esc(value_fmt(v))}</title></circle>')

    return (f'<svg class="sp-chart" viewBox="0 0 {W} {H}" '
            f'aria-label="{_esc(aria)}">'
            f'{"".join(grid)}{"".join(xlab)}{"".join(paths)}{"".join(dots)}</svg>')


def _bandwidth_mini_section(site_key: str, timeseries_rows: list[dict]) -> str:
    pts = sorted(
        [(r["date"], float(r.get("bandwidth_gb") or 0))
         for r in timeseries_rows if r.get("key") == site_key and r.get("bandwidth_gb") is not None],
        key=lambda x: x[0])
    if len(pts) < 2:
        return ('<section class="sp-panel"><h2>Bandwidth trend</h2>'
                '<p class="muted">history building &mdash; need at least 2 snapshots, '
                f'have {len(pts)}.</p></section>')

    dates = [d for d, _ in pts]
    values = [v for _, v in pts]
    peak = max(values)
    chart = _svg_line_chart(
        [{"color": "#2563eb", "points": values}], dates,
        value_fmt=lambda v: f"{v:,.1f} GB", aria="Bandwidth trend")
    return f'''<section class="sp-panel"><h2>Bandwidth trend
        <span class="muted">&middot; last {len(pts)} snapshots &middot; GB</span></h2>
        {chart}
        <p class="muted">
          <span class="bh-swatch" style="background:#2563eb"></span> bandwidth &middot;
          {_esc(dates[0])} &rarr; {_esc(dates[-1])} &middot; peak {peak:,.0f} GB
        </p>
      </section>'''


def _requests_threats_section(per_site: dict | None) -> str:
    """Requests vs CF threats (security actions) over 30 days.

    Replaces the original "bot vs human" chart: CF Bot Management is a paid
    add-on absent on our plan tier fleet-wide, so a true bot/human split is
    not available. Threats — requests CF challenged or blocked — IS available
    on every zone and is the honest security-pressure signal.
    """
    if not per_site:
        return ('<section class="sp-panel"><h2>Requests &amp; threats (30d)</h2>'
                '<p class="muted">per-site analytics not yet collected &mdash; '
                'click <strong>&#x27F3; Refresh this site</strong> in the header '
                'to pull fresh CF data, or wait for the next daily run.</p></section>')
    series = per_site.get("requests_threats_daily") or []
    if not series:
        return ('<section class="sp-panel"><h2>Requests &amp; threats (30d)</h2>'
                '<p class="muted">no traffic recorded in this window.</p></section>')

    dates = [d["date"] for d in series]
    total_threats = sum(d["threats"] for d in series)
    chart = _svg_line_chart(
        [{"color": "#2563eb", "points": [d["requests"] for d in series]},
         {"color": "#dc2626", "points": [d["threats"] for d in series]}],
        dates, value_fmt=lambda v: f"{v:,.0f}", aria="Requests and threats")

    return f'''<section class="sp-panel"><h2>Requests &amp; threats (30d)
        <span class="muted">&middot; threats = requests Cloudflare challenged or blocked</span></h2>
        {chart}
        <p class="muted">
          <span class="bh-swatch" style="background:#2563eb"></span> requests &middot;
          <span class="bh-swatch" style="background:#dc2626"></span> threats &middot;
          {_esc(series[0]["date"])} &rarr; {_esc(series[-1]["date"])} &middot;
          {total_threats:,} threats total
        </p>
      </section>'''


def _top_countries_section(per_site: dict | None) -> str:
    if not per_site:
        return ""    # parent already showed the "not yet collected" panel
    countries = per_site.get("countries") or []
    # Honest denominator: true 30-day request total from the daily rollup.
    total = sum(d.get("requests", 0)
                for d in (per_site.get("requests_threats_daily") or []))
    if not countries:
        return ('<section class="sp-panel"><h2>Top countries (30d)</h2>'
                '<p class="muted">no country data in this window.</p></section>')
    peak = max(c["requests"] for c in countries) or 1
    rows = []
    for c in countries:
        bar_pct = c["requests"] / peak * 100
        pct_total = (c["requests"] / total * 100) if total > 0 else 0
        rows.append(
            f'<div class="country-row">'
            f'<span class="country-name">{_esc(c["country"])}</span>'
            f'<div class="country-bar"><span class="country-fill" '
            f'style="width:{bar_pct:.1f}%"></span></div>'
            f'<span class="country-num">{c["requests"]:,}</span>'
            f'<span class="country-pct muted">{pct_total:.1f}%</span>'
            f'</div>')
    return (f'<section class="sp-panel"><h2>Top countries (30d) '
            f'<span class="muted">&middot; {len(countries)} shown</span></h2>'
            f'<div class="country-list">{"".join(rows)}</div></section>')


def _top_paths_section(per_site: dict | None) -> str:
    if not per_site:
        return ""
    paths = per_site.get("top_paths") or []
    if not paths:
        return ('<section class="sp-panel"><h2>Top paths (7d)</h2>'
                '<p class="muted">no path data in this window.</p></section>')
    rows = "".join(
        f'<tr><td class="path-cell">{_esc(p["path"])}</td>'
        f'<td class="num">{p["requests"]:,}</td>'
        f'<td class="num muted">{p["pct_of_total"]:.1f}%</td></tr>'
        for p in paths)
    return (f'<section class="sp-panel"><h2>Top paths (7d) '
            f'<span class="muted">&middot; top {len(paths)}</span></h2>'
            f'<table class="sp-table"><thead><tr><th>Path</th>'
            f'<th class="num">Requests</th><th class="num">% of total</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></section>')


def _top_uas_section(per_site: dict | None) -> str:
    if not per_site:
        return ""
    uas = per_site.get("top_uas") or []
    if not uas:
        return ('<section class="sp-panel"><h2>Top user agents (7d)</h2>'
                '<p class="muted">no UA data in this window.</p></section>')
    rows = []
    for u in uas:
        ua = u["ua"]
        ua_short = ua if len(ua) <= 80 else ua[:77] + "..."
        bot_pill = ('<span class="ua-bot">BOT</span>'
                    if u.get("is_bot") else '<span class="muted">human</span>')
        rows.append(
            f'<tr><td class="ua-cell" title="{_esc(ua)}">{_esc(ua_short)}</td>'
            f'<td>{bot_pill}</td>'
            f'<td class="num">{u["requests"]:,}</td>'
            f'<td class="num muted">{u["pct_of_total"]:.1f}%</td></tr>')
    return (f'<section class="sp-panel"><h2>Top user agents (7d) '
            f'<span class="muted">&middot; top {len(uas)} &middot; '
            f'Type inferred from the user-agent string, not a Cloudflare bot score</span></h2>'
            f'<table class="sp-table"><thead><tr><th>User agent</th><th>Type</th>'
            f'<th class="num">Requests</th><th class="num">% of total</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></section>')


def _footer(per_site: dict | None) -> str:
    if not per_site:
        return ('<footer class="sp-foot muted">CF GraphQL data not yet collected '
                'for this site.</footer>')
    return (f'<footer class="sp-foot muted">source: CF GraphQL '
            f'<code>httpRequests1dGroups</code> (countries + threats, 30d) + '
            f'<code>httpRequestsAdaptiveGroups</code> (paths + user agents, 7d) '
            f'&middot; last refreshed '
            f'<strong>{_esc(per_site.get("fetched_at","unknown"))}</strong>'
            f'</footer>')


def _storage_trend_section(site: dict) -> str:
    """Two-line storage trend (files vs database, GB) from the wpe.daily array.

    Skipped entirely for cf-only sites (no WPE data). Empty state when fewer
    than 2 dated daily records exist.
    """
    wpe = site.get("wpe") or {}
    if not wpe:
        return ""                       # cf-only site — no WPE storage data
    daily = sorted([d for d in (wpe.get("daily") or []) if d.get("date")],
                   key=lambda d: d["date"])
    if len(daily) < 2:
        return ('<section class="sp-panel"><h2>Storage trend</h2>'
                '<p class="muted">history building &mdash; need at least 2 days, '
                f'have {len(daily)}.</p></section>')

    dates = [d["date"] for d in daily]
    files = [int(d.get("storage_file_bytes") or 0) / 1e9 for d in daily]
    database = [int(d.get("storage_database_bytes") or 0) / 1e9 for d in daily]
    chart = _svg_line_chart(
        [{"color": "#2563eb", "points": files},
         {"color": "#d97706", "points": database}],
        dates, value_fmt=lambda v: f"{v:,.2f} GB", aria="Storage trend")
    latest_total = files[-1] + database[-1]
    return f'''<section class="sp-panel"><h2>Storage trend
        <span class="muted">&middot; files vs database, GB</span></h2>
        {chart}
        <p class="muted">
          <span class="bh-swatch" style="background:#2563eb"></span> files &middot;
          <span class="bh-swatch" style="background:#d97706"></span> database &middot;
          {_esc(dates[0])} &rarr; {_esc(dates[-1])} &middot;
          latest total {latest_total:,.2f} GB
        </p>
      </section>'''


def render_site_page(site: dict, snapshot: dict, timeseries_rows: list[dict]) -> str:
    """Return a self-contained per-site HTML page. No external assets."""
    join = site.get("join_state", "unknown")
    notice = ""
    if join == "wpe-only":
        notice = _notice("Not behind our Cloudflare — CF charts unavailable for this site.")
    elif join == "cf-only":
        notice = _notice("Not on WP Engine — bandwidth and visit metrics unavailable for this site.")

    cf_section = _cf_config_section(site["cf"]) if site.get("cf") else ""
    bw_section = _bandwidth_mini_section(site["key"], timeseries_rows)
    storage_section = _storage_trend_section(site)
    bh_section = _requests_threats_section((site.get("cf") or {}).get("per_site"))
    countries_section = _top_countries_section((site.get("cf") or {}).get("per_site"))
    paths_section = _top_paths_section((site.get("cf") or {}).get("per_site"))
    uas_section = _top_uas_section((site.get("cf") or {}).get("per_site"))
    foot = _footer((site.get("cf") or {}).get("per_site"))

    body = f'''
    {_header(site)}
    {notice}
    {_quick_stats(site, snapshot)}
    {_analytics_card(site)}
    {_active_alerts(site["key"], snapshot)}
    {cf_section}
    {bw_section}
    {storage_section}
    {bh_section}
    {countries_section}
    {paths_section}
    {uas_section}
    {foot}'''
    return _wrap(site["key"], body)


def write_all_site_pages(snapshot: dict, timeseries_rows: list[dict]) -> int:
    """Render one HTML page per site in the snapshot to SITES_DIR. Returns count written."""
    SITES_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for site in snapshot.get("sites", []):
        if not site.get("key"):
            continue
        html = render_site_page(site, snapshot, timeseries_rows)
        path = SITES_DIR / f"{safe_key(site['key'])}.html"
        path.write_text(html, encoding="utf-8")
        n += 1
    return n


def _wrap(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} &middot; Fleet Monitor</title>
<style>{_SP_CSS}</style></head>
<body><main class="sp-main">{body}</main>
<script>{_SP_JS}</script>
</body></html>"""


_SP_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:0;font-family:"Segoe UI Variable Text",-apple-system,system-ui,sans-serif;
     background:#f6f7f9;color:#14151a;font-size:14px;line-height:1.5}
.sp-main{max-width:1200px;margin:0 auto;padding:24px}
.sp-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;gap:16px;flex-wrap:wrap}
.sp-head-left{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.sp-back{color:#6a6f78;text-decoration:none;font-size:13px}
.sp-back:hover{color:#14151a;text-decoration:underline}
.sp-title{font-size:24px;margin:0;font-weight:700;letter-spacing:-.01em}
.join-pill{padding:2px 10px;border-radius:999px;font-size:11px;font-weight:600;background:#eef4ff;color:#2563eb;text-transform:uppercase;letter-spacing:.04em}
.join-wpe-only{background:#fef7e6;color:#d97706}
.join-cf-only{background:#eafbe9;color:#16a34a}
.sp-refresh-btn{background:#c8f250;color:#14151a;border:0;padding:8px 14px;border-radius:999px;font:inherit;font-weight:600;cursor:pointer}
.sp-refresh-btn:hover{background:#b3df3f}
.sp-refresh-btn[disabled]{opacity:.55;cursor:wait}
.sp-notice{padding:10px 14px;background:#fff7e6;border:1px solid #f3e0a3;border-radius:8px;margin-bottom:16px;color:#7a5a05}
.ss-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px}
.ss-card{background:#fff;border:1px solid #ececef;border-radius:12px;padding:14px 16px;display:flex;flex-direction:column;gap:4px;position:relative}
.ss-card-ok{border-color:#bbf7d0}
.ss-card-watch{border-color:#fde68a}
.ss-card-bad{border-color:#fecaca}
.ss-label{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
.ss-value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.ss-value-ok{color:#15803d}
.ss-value-watch{color:#b45309}
.ss-value-bad{color:#b91c1c}
.ss-sub{font-size:11.5px}
.ss-pill{position:absolute;top:12px;right:14px;font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:2px 8px;border-radius:999px}
.ss-pill-ok{background:#dcfce7;color:#15803d}
.ss-pill-watch{background:#fef3c7;color:#b45309}
.ss-pill-bad{background:#fee2e2;color:#b91c1c}
.muted{color:#6a6f78}
.sp-panel{background:#fff;border:1px solid #ececef;border-radius:12px;padding:18px 20px;margin-bottom:16px}
.sp-panel h2{font-size:14px;margin:0 0 12px;font-weight:600;letter-spacing:-.005em}
.alert-list{margin:0;padding:0;list-style:none}
.alert-row{padding:8px 0;border-bottom:1px solid #f1f2f4;font-size:13px}
.alert-row:last-child{border-bottom:0}
.sev-pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-right:6px}
.sev-pill.sev-critical{background:#fef2f2;color:#dc2626}
.sev-pill.sev-warning{background:#fef7e6;color:#d97706}
.sev-pill.sev-info{background:#eef4ff;color:#2563eb}
.cf-dl{display:grid;grid-template-columns:160px 1fr;gap:6px 16px;margin:0;font-size:13px}
.cf-dl dt{font-weight:500;color:#6a6f78}
.cf-dl dd{margin:0;font-variant-numeric:tabular-nums}
.bh-swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}
.sp-chart{width:100%;height:auto;display:block;margin:10px 0 4px;
  background:linear-gradient(180deg,#fafbfc 0%,#ffffff 100%);
  border:1px solid #f1f2f4;border-radius:8px}
.sp-grid{stroke:#eceef1;stroke-width:1;shape-rendering:crispEdges}
.sp-axis{font-family:ui-monospace,"SF Mono",Consolas,monospace;
  font-size:9.5px;fill:#9aa0aa}
.country-list{display:flex;flex-direction:column;gap:6px}
.country-row{display:grid;grid-template-columns:48px 1fr 90px 60px;align-items:center;gap:10px;font-size:13px;font-variant-numeric:tabular-nums}
.country-name{font-weight:600}
.country-bar{background:#f1f2f4;border-radius:4px;height:10px;overflow:hidden}
.country-fill{display:block;height:100%;background:#7c3aed;border-radius:4px}
.country-num{text-align:right}
.country-pct{text-align:right;font-size:12px}
.sp-table{width:100%;border-collapse:collapse;font-size:13px}
.sp-table th,.sp-table td{padding:8px 10px;text-align:left;border-bottom:1px solid #f1f2f4}
.sp-table th{font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:#6a6f78}
.sp-table .num{text-align:right;font-variant-numeric:tabular-nums}
.path-cell,.ua-cell{font-family:ui-monospace,"SF Mono",Consolas,monospace;font-size:12px;word-break:break-all}
.ua-bot{display:inline-block;padding:1px 7px;border-radius:999px;background:#fef2f2;color:#dc2626;font-size:10.5px;font-weight:700;letter-spacing:.04em}
.sp-foot{margin-top:24px;padding-top:14px;border-top:1px solid #ececef;font-size:11.5px;text-align:center}
.sp-foot code{font-family:ui-monospace,"SF Mono",Consolas,monospace;background:#f1f2f4;padding:1px 5px;border-radius:3px}
.an-cols{display:grid;grid-template-columns:1fr 1fr;gap:22px}
@media (max-width:680px){.an-cols{grid-template-columns:1fr}}
.an-sub{display:flex;flex-direction:column;gap:10px}
.an-head{display:flex;align-items:baseline;justify-content:space-between;gap:10px;border-bottom:1px solid #ececef;padding-bottom:6px}
.an-head h3{margin:0;font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:#6a6f78}
.an-src{font-size:10.5px;color:#9aa0aa;font-style:italic}
.an-rows{display:grid;grid-template-columns:auto 1fr auto;column-gap:14px;row-gap:7px;font-size:13px;align-items:baseline}
.an-k{color:#6a6f78}
.an-v{font-weight:600;font-variant-numeric:tabular-nums;text-align:right}
.an-d{font-size:12px;font-variant-numeric:tabular-nums;color:#9aa0aa;white-space:nowrap;text-align:right;justify-self:end}
.an-d.an-down{color:#b91c1c;font-weight:600}
.an-d.an-up{color:#047857;font-weight:600}
.an-note{color:#9aa0aa;font-weight:400;margin-left:4px}
.an-empty{font-size:13px;margin:6px 0 0}
"""


_SP_JS = """
async function refreshSite(key){
  var btn=document.getElementById('sp-refresh');
  if(location.protocol==='file:'){
    alert('Refresh only works when this page is served via http://localhost:8765/.\\n\\nStart the server with:\\n  python -m projects.fleet_monitoring.serve');
    return;
  }
  if(btn){btn.disabled=true;btn.textContent='Refreshing...';}
  try{
    var r=await fetch('/refresh-site',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:key})});
    if(r.ok){location.reload();}
    else{var e=await r.json().catch(function(){return {error:r.statusText};});
         alert('Refresh failed: '+(e.error||'unknown error'));}
  }catch(err){alert('Refresh failed: '+err.message);}
  finally{if(btn){btn.disabled=false;btn.textContent='\\u27F3 Refresh this site';}}
}
"""
