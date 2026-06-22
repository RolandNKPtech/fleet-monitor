from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from skills.cloudflare.push_validator_allowlist import RULE_DESCRIPTION as VALIDATOR_RULE_DESCRIPTION
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.check_waf")

STANDARD_EXPRESSION = '(ip.src.country ne "US" and not cf.client.bot)'
STANDARD_ACTION = "managed_challenge"


class CheckWafSkill(BaseSkill):
    name = "cloudflare.check_waf"
    description = "Audit custom WAF rules — check for standard non-US challenge rule"
    required_inputs = ["target"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)
            ruleset = await client.get_ruleset(zone_id, "http_request_firewall_custom")

            rules = []
            challenge_rule_exists = False
            expression_matches = False
            validator_allowlist_exists = False
            validator_allowlist_position = None

            if ruleset and ruleset.get("rules"):
                for idx, rule in enumerate(ruleset["rules"]):
                    expr = rule.get("expression", "")
                    action = rule.get("action", "")
                    enabled = rule.get("enabled", True)
                    description = rule.get("description", "")

                    is_challenge = STANDARD_EXPRESSION in expr or expr in STANDARD_EXPRESSION
                    if is_challenge or action == STANDARD_ACTION:
                        challenge_rule_exists = True
                        expression_matches = expr == STANDARD_EXPRESSION

                    is_validator_allowlist = description == VALIDATOR_RULE_DESCRIPTION
                    if is_validator_allowlist:
                        validator_allowlist_exists = True
                        validator_allowlist_position = idx

                    rules.append({
                        "description": description,
                        "expression": expr,
                        "action": action,
                        "enabled": enabled,
                        "is_standard_challenge": is_challenge,
                        "is_validator_allowlist": is_validator_allowlist,
                    })

            issues = []
            if not challenge_rule_exists:
                issues.append("No non-US challenge rule found")
            elif not expression_matches:
                issues.append("Challenge rule expression doesn't match standard")

            if not validator_allowlist_exists:
                issues.append("Validator + crawler allowlist rule missing")
            elif validator_allowlist_position is not None and validator_allowlist_position > 0:
                # Allowlist must run BEFORE country challenge to be effective
                challenge_positions = [
                    i for i, r in enumerate(rules) if r["is_standard_challenge"]
                ]
                if challenge_positions and validator_allowlist_position > min(challenge_positions):
                    issues.append(
                        "Validator allowlist sits after country challenge — wrong order"
                    )

            # Check for disabled rules
            for r in rules:
                if r["is_standard_challenge"] and not r["enabled"]:
                    issues.append("Challenge rule exists but is disabled")
                if r["is_validator_allowlist"] and not r["enabled"]:
                    issues.append("Validator allowlist rule exists but is disabled")

            status = SkillStatus.SUCCESS if not issues else SkillStatus.WARNING
            return SkillResult(
                status=status,
                data={
                    "domain": target,
                    "challenge_rule_exists": challenge_rule_exists,
                    "expression_matches": expression_matches,
                    "validator_allowlist_exists": validator_allowlist_exists,
                    "validator_allowlist_position": validator_allowlist_position,
                    "total_custom_rules": len(rules),
                    "rules": rules,
                    "issues": issues,
                },
                message=f"{target}: WAF {'OK' if not issues else ', '.join(issues)}",
            )

        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"WAF check failed for {target}: {e}",
                errors=[str(e)],
            )
