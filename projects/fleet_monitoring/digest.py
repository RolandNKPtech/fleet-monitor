"""Cloudflare config digest normalization and drift diffing.

A digest is a stable, diffable view of a zone's CF config. Volatile fields
(rule IDs, timestamps, refs) are stripped so two snapshots compare cleanly.
"""
from __future__ import annotations

# Severity-shaping: which changes are which kind.
_SSL_RANK = {"off": 0, "flexible": 1, "full": 2, "strict": 3}
_TLS_RANK = {"1.0": 0, "1.1": 1, "1.2": 2, "1.3": 3}


def normalize_rules(rules: list[dict] | None) -> list[dict]:
    """Reduce raw CF rules to {description, expression, action, enabled}."""
    out = []
    for r in rules or []:
        out.append({
            "description": r.get("description") or "",
            "expression": r.get("expression") or "",
            "action": r.get("action") or "",
            "enabled": bool(r.get("enabled", True)),
        })
    return out


def build_digest(settings: dict, bot: dict, waf_rules: list[dict],
                 cache_rules: list[dict], dns_proxy_apex: bool | None,
                 dns_proxy_www: bool | None) -> dict:
    """Assemble a zone's normalized config digest."""
    return {
        "settings": {
            "ssl": settings.get("ssl"),
            "min_tls_version": settings.get("min_tls_version"),
        },
        "bot": {
            "ai_bots_protection": bot.get("ai_bots_protection"),
            "crawler_protection": bot.get("crawler_protection"),
            "bot_fight_mode": bot.get("bot_fight_mode"),
        },
        "waf_rules": normalize_rules(waf_rules),
        "cache_rules": normalize_rules(cache_rules),
        "dns_proxy_apex": dns_proxy_apex,
        "dns_proxy_www": dns_proxy_www,
    }


def _rule_key(rule: dict) -> str:
    return rule.get("description") or rule.get("expression") or ""


def digest_diff(old: dict, new: dict) -> list[dict]:
    """Compare two digests. Returns [{field, old, new, kind, severity}, ...].

    kind values: ssl_downgrade, ssl_change, tls_downgrade, tls_change,
    bot_toggle_disabled, bot_toggle_changed, dns_proxy_lost,
    waf_rule_added, waf_rule_removed, waf_rule_changed,
    cache_rule_added, cache_rule_removed, cache_rule_changed.
    """
    changes: list[dict] = []
    if not old:
        return changes  # first observation — nothing to diff against

    os_, ns = old.get("settings", {}), new.get("settings", {})

    # SSL
    if os_.get("ssl") != ns.get("ssl"):
        downgrade = _SSL_RANK.get(ns.get("ssl"), 9) < _SSL_RANK.get(os_.get("ssl"), 0)
        changes.append({"field": "ssl", "old": os_.get("ssl"), "new": ns.get("ssl"),
                        "kind": "ssl_downgrade" if downgrade else "ssl_change",
                        "severity": "critical" if downgrade else "info"})
    # min TLS
    if os_.get("min_tls_version") != ns.get("min_tls_version"):
        downgrade = (_TLS_RANK.get(ns.get("min_tls_version"), 9)
                     < _TLS_RANK.get(os_.get("min_tls_version"), 0))
        changes.append({"field": "min_tls_version", "old": os_.get("min_tls_version"),
                        "new": ns.get("min_tls_version"),
                        "kind": "tls_downgrade" if downgrade else "tls_change",
                        "severity": "warning" if downgrade else "info"})
    # Bot toggles
    for k in ("ai_bots_protection", "crawler_protection", "bot_fight_mode"):
        ov, nv = old.get("bot", {}).get(k), new.get("bot", {}).get(k)
        if ov != nv:
            disabled = nv in (None, "off", "disabled", False)
            changes.append({"field": f"bot.{k}", "old": ov, "new": nv,
                            "kind": "bot_toggle_disabled" if disabled else "bot_toggle_changed",
                            "severity": "warning" if disabled else "info"})
    # DNS proxy
    for k in ("dns_proxy_apex", "dns_proxy_www"):
        ov, nv = old.get(k), new.get(k)
        if ov and not nv:
            changes.append({"field": k, "old": ov, "new": nv, "kind": "dns_proxy_lost",
                            "severity": "critical"})
        elif ov != nv:
            changes.append({"field": k, "old": ov, "new": nv, "kind": "dns_proxy_changed",
                            "severity": "info"})
    # Rule lists
    for list_name, kind_prefix in (("waf_rules", "waf_rule"), ("cache_rules", "cache_rule")):
        old_by = {_rule_key(r): r for r in old.get(list_name, [])}
        new_by = {_rule_key(r): r for r in new.get(list_name, [])}
        for key in old_by.keys() - new_by.keys():
            changes.append({"field": f"{list_name}:{key}", "old": old_by[key], "new": None,
                            "kind": f"{kind_prefix}_removed", "severity": "warning"})
        for key in new_by.keys() - old_by.keys():
            changes.append({"field": f"{list_name}:{key}", "old": None, "new": new_by[key],
                            "kind": f"{kind_prefix}_added", "severity": "info"})
        for key in old_by.keys() & new_by.keys():
            if old_by[key] != new_by[key]:
                changes.append({"field": f"{list_name}:{key}", "old": old_by[key],
                                "new": new_by[key], "kind": f"{kind_prefix}_changed",
                                "severity": "warning"})
    return changes
