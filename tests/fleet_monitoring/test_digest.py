from projects.fleet_monitoring.digest import normalize_rules, digest_diff


def test_normalize_rules_strips_volatile_fields():
    raw = [
        {"id": "abc123", "description": "NKP allowlist", "expression": "cf.client.bot",
         "action": "skip", "enabled": True, "last_updated": "2026-05-01", "ref": "xyz"},
    ]
    norm = normalize_rules(raw)
    assert norm == [{"description": "NKP allowlist", "expression": "cf.client.bot",
                     "action": "skip", "enabled": True}]


def test_digest_diff_detects_setting_and_rule_changes():
    old = {
        "settings": {"ssl": "strict", "min_tls_version": "1.2"},
        "bot": {"ai_bots_protection": "block"},
        "waf_rules": [{"description": "allowlist", "expression": "a", "action": "skip", "enabled": True}],
        "cache_rules": [],
        "dns_proxy_www": True,
    }
    new = {
        "settings": {"ssl": "full", "min_tls_version": "1.2"},          # ssl downgraded
        "bot": {"ai_bots_protection": "block"},
        "waf_rules": [],                                                 # rule removed
        "cache_rules": [],
        "dns_proxy_www": True,
    }
    changes = digest_diff(old, new)
    kinds = {c["kind"] for c in changes}
    assert "ssl_downgrade" in kinds
    assert "waf_rule_removed" in kinds
    ssl_change = next(c for c in changes if c["kind"] == "ssl_downgrade")
    assert ssl_change["old"] == "strict" and ssl_change["new"] == "full"
