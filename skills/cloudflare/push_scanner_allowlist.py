"""Push the NKP internal-scanner allowlist Skip rule to a CF zone's custom WAF
ruleset.

The rule matches our scanner User-Agent ("NKP-Fleet-Scanner") and skips:
  - SBFM (so the scanner UA isn't fingerprinted as suspicious)
  - rate-limit (so a full-fleet scan isn't throttled)
  - managed firewall (so legacy managed rules don't block)
  - other security products (zone lockdown, UA block, BIC, hot, security level)

Country challenge rules in the *current* ruleset are bypassed via "skip:
ruleset=current". Real visitors still face the full WAF gauntlet.

Idempotent by description. Fleet-deployed via scripts/deploy_scanner_allowlist.py
and bundled into the AI search composer (skills.cloudflare.setup_ai_search) so
every new zone gets it automatically.

Why two allowlists? "NKP: validator + crawler allowlist" matches public crawlers
(Google, AI bots, validators). This one matches OUR internal scanners only.
Splitting them keeps the public-crawler rule audit-friendly and lets us rotate
the scanner UA without disturbing the crawler list.
"""
from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.push_scanner_allowlist")

RULE_DESCRIPTION = "NKP: scanner allowlist"
SCANNER_UA_SUBSTRING = "NKP-Fleet-Scanner"
RULE_EXPRESSION = f'(http.user_agent contains "{SCANNER_UA_SUBSTRING}")'

# Broader skip scope than the validator rule — internal scanners need to bypass
# rate-limit + managed firewall + country challenge for unrestricted audit.
RULE_BODY = {
    "action": "skip",
    "action_parameters": {
        "ruleset": "current",
        "phases": [
            "http_ratelimit",
            "http_request_sbfm",
            "http_request_firewall_managed",
        ],
        "products": [
            "waf",
            "rateLimit",
            "securityLevel",
            "hot",
            "bic",
            "uaBlock",
            "zoneLockdown",
        ],
    },
    "expression": RULE_EXPRESSION,
    "description": RULE_DESCRIPTION,
    "enabled": True,
}

VALID_MODES = {"apply", "remove", "dry_run"}


class PushScannerAllowlistSkill(BaseSkill):
    name = "cloudflare.push_scanner_allowlist"
    description = (
        "Push/remove the NKP internal-scanner Skip rule on a CF zone. Matches "
        "the NKP-Fleet-Scanner UA. Idempotent."
    )
    required_inputs = ["target"]
    optional_inputs = ["mode"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        mode = kwargs.get("mode", "dry_run")

        if mode not in VALID_MODES:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Invalid mode '{mode}'. Must be one of {sorted(VALID_MODES)}",
                errors=[f"mode must be one of {sorted(VALID_MODES)}"],
            )

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)
            ruleset = await client.get_ruleset(zone_id, "http_request_firewall_custom")
        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"CF API error for {target}: {e}",
                errors=[str(e)],
            )

        if ruleset is None:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: no custom WAF ruleset exists on this zone",
                errors=["http_request_firewall_custom ruleset not found"],
            )

        ruleset_id = ruleset["id"]
        existing_rules = ruleset.get("rules", []) or []
        existing = next(
            (r for r in existing_rules if r.get("description") == RULE_DESCRIPTION),
            None,
        )

        base_data = {
            "domain": target,
            "zone_id": zone_id,
            "ruleset_id": ruleset_id,
            "existing_rules_count": len(existing_rules),
            "already_present": existing is not None,
        }

        if mode == "dry_run":
            base_data["action_taken"] = "none"
            base_data["would_add"] = existing is None
            base_data["would_remove"] = existing is not None
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: dry run — "
                + ("already present" if existing else "would add"),
            )

        if mode == "remove":
            if existing is None:
                base_data["action_taken"] = "not_present"
                return SkillResult(
                    status=SkillStatus.SUCCESS,
                    data=base_data,
                    message=f"{target}: rule not present, nothing to remove",
                )
            try:
                await client.delete(
                    f"/zones/{zone_id}/rulesets/{ruleset_id}/rules/{existing['id']}"
                )
            except APIError as e:
                return SkillResult(
                    status=SkillStatus.FAILURE,
                    message=f"{target}: delete failed — {e}",
                    errors=[str(e)],
                )
            base_data["action_taken"] = "removed"
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: scanner allowlist rule removed",
            )

        # mode == "apply"
        if existing is not None:
            if not existing.get("enabled"):
                # Re-enable a disabled instance
                try:
                    await client.patch(
                        f"/zones/{zone_id}/rulesets/{ruleset_id}/rules/{existing['id']}",
                        json={**RULE_BODY},
                    )
                except APIError as e:
                    return SkillResult(
                        status=SkillStatus.FAILURE,
                        message=f"{target}: re-enable failed — {e}",
                        errors=[str(e)],
                    )
                base_data["action_taken"] = "re_enabled"
                return SkillResult(
                    status=SkillStatus.SUCCESS,
                    data=base_data,
                    message=f"{target}: scanner allowlist rule re-enabled",
                )
            base_data["action_taken"] = "already_present"
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: rule already present, nothing to do",
            )

        body = dict(RULE_BODY)
        if existing_rules:
            body["position"] = {"before": existing_rules[0]["id"]}

        try:
            resp = await client.post(
                f"/zones/{zone_id}/rulesets/{ruleset_id}/rules",
                json=body,
            )
        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: apply failed — {e}",
                errors=[str(e)],
            )

        new_rules = resp.get("result", {}).get("rules", []) or []
        added = next(
            (r for r in new_rules if r.get("description") == RULE_DESCRIPTION), None
        )
        base_data["action_taken"] = "added"
        base_data["new_rule_id"] = added.get("id") if added else None
        base_data["rules_after"] = len(new_rules)

        return SkillResult(
            status=SkillStatus.SUCCESS,
            data=base_data,
            message=f"{target}: scanner allowlist rule added",
        )
