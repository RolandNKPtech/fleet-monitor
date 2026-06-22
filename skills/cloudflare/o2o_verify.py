from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.o2o_verify")

EXPECTED_CNAME_TARGET = "wp.wpenginepowered.com"


def _check(name: str, passed: bool, actual, expected, fix: str | None = None) -> dict:
    result = {"name": name, "passed": passed, "actual": actual, "expected": expected}
    if not passed and fix:
        result["fix"] = fix
    return result


class O2OVerifySkill(BaseSkill):
    name = "cloudflare.o2o_verify"
    description = "Run 10-point O2O readiness check on a domain"
    required_inputs = ["target"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)

            # Fetch all data concurrently
            import asyncio
            settings_task = asyncio.create_task(client.get_zone_settings(zone_id))
            tiered_task = asyncio.create_task(
                client.get(f"/zones/{zone_id}/cache/tiered_cache_smart_topology_enable")
            )
            cache_ruleset_task = asyncio.create_task(
                client.get_ruleset(zone_id, "http_request_cache_settings")
            )
            dns_task = asyncio.create_task(
                client.get(f"/zones/{zone_id}/dns_records")
            )
            waf_ruleset_task = asyncio.create_task(
                client.get_ruleset(zone_id, "http_request_firewall_custom")
            )

            settings, tiered_resp, cache_ruleset, dns_data, waf_ruleset = await asyncio.gather(
                settings_task, tiered_task, cache_ruleset_task, dns_task, waf_ruleset_task
            )

            checks = []

            # 1. SSL = strict
            ssl_val = settings.get("ssl", "unknown")
            checks.append(_check(
                "ssl_strict",
                ssl_val == "strict",
                ssl_val, "strict",
                fix="Set SSL/TLS encryption mode to Full (Strict)" if ssl_val != "strict" else None,
            ))

            # 2. Always HTTPS = on
            always_https = settings.get("always_use_https", "off")
            checks.append(_check(
                "always_https",
                always_https == "on",
                always_https, "on",
                fix="Enable Always Use HTTPS" if always_https != "on" else None,
            ))

            # 3. HSTS enabled + max_age=31536000
            hsts_raw = settings.get("security_header", {})
            sts = hsts_raw.get("strict_transport_security", {}) if isinstance(hsts_raw, dict) else {}
            hsts_enabled = bool(sts.get("enabled", False))
            hsts_max_age = sts.get("max_age", 0)
            hsts_ok = hsts_enabled and hsts_max_age == 31536000
            checks.append(_check(
                "hsts",
                hsts_ok,
                {"enabled": hsts_enabled, "max_age": hsts_max_age},
                {"enabled": True, "max_age": 31536000},
                fix="Enable HSTS with max-age=31536000" if not hsts_ok else None,
            ))

            # 4. APO disabled
            apo_raw = settings.get("automatic_platform_optimization", {})
            apo_enabled = apo_raw.get("enabled", False) if isinstance(apo_raw, dict) else False
            checks.append(_check(
                "apo_disabled",
                not apo_enabled,
                apo_enabled, False,
                fix="Disable APO" if apo_enabled else None,
            ))

            # 5. Rocket Loader = off
            rl = settings.get("rocket_loader", "off")
            checks.append(_check(
                "rocket_loader_off",
                rl == "off",
                rl, "off",
                fix="Disable Rocket Loader" if rl != "off" else None,
            ))

            # 6. Early Hints = on
            eh = settings.get("early_hints", "off")
            checks.append(_check(
                "early_hints_on",
                eh == "on",
                eh, "on",
                fix="Enable Early Hints" if eh != "on" else None,
            ))

            # 7. Smart Tiered Cache = on
            tiered_value = tiered_resp.get("result", {}).get("value", "off")
            checks.append(_check(
                "smart_tiered_cache_on",
                tiered_value == "on",
                tiered_value, "on",
                fix="Enable Smart Tiered Cache Topology" if tiered_value != "on" else None,
            ))

            # 8. Cache rule exists
            cache_rule_exists = (
                cache_ruleset is not None
                and len(cache_ruleset.get("rules", [])) > 0
            )
            checks.append(_check(
                "cache_rule_exists",
                cache_rule_exists,
                "exists" if cache_rule_exists else "missing", "exists",
                fix="Create a cache rule for HTML pages" if not cache_rule_exists else None,
            ))

            # 9. www DNS correct
            records = dns_data.get("result", [])
            www_ok = any(
                r.get("type") == "CNAME"
                and r.get("content") == EXPECTED_CNAME_TARGET
                and r.get("proxied", False)
                for r in records
                if r.get("name", "").startswith("www.")
            )
            actual_www = next(
                ({"type": r["type"], "content": r["content"], "proxied": r.get("proxied")}
                 for r in records if r.get("name", "").startswith("www.")),
                "missing",
            )
            checks.append(_check(
                "www_dns_correct",
                www_ok,
                actual_www,
                {"type": "CNAME", "content": EXPECTED_CNAME_TARGET, "proxied": True},
                fix=f"Set www CNAME to {EXPECTED_CNAME_TARGET} (proxied)" if not www_ok else None,
            ))

            # Detect O2O level: WAF rule present = full, absent = lite
            waf_rule_exists = (
                waf_ruleset is not None
                and len(waf_ruleset.get("rules", [])) > 0
            )
            o2o_level = "full" if waf_rule_exists else "lite"

            # 10. WAF challenge rule (full only)
            if o2o_level == "full":
                checks.append(_check(
                    "waf_challenge_rule",
                    waf_rule_exists,
                    "exists" if waf_rule_exists else "missing", "exists",
                    fix="Add WAF challenge rule for non-US traffic" if not waf_rule_exists else None,
                ))

            total = len(checks)
            passed = sum(1 for c in checks if c["passed"])
            all_passed = passed == total

            status = SkillStatus.SUCCESS if all_passed else SkillStatus.WARNING
            return SkillResult(
                status=status,
                data={
                    "domain": target,
                    "zone_id": zone_id,
                    "o2o_level": o2o_level,
                    "checks": checks,
                    "passed": passed,
                    "total": total,
                },
                message=f"{target}: {passed}/{total} checks passed",
            )

        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"O2O verify failed for {target}: {e}",
                errors=[str(e)],
            )
