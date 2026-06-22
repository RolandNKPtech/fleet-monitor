"""Push a validator/crawler allowlist Skip rule to a CF zone's custom WAF ruleset.

The rule lets Schema.org validator, Google Rich Results Test, PageSpeed,
Lighthouse, and social-media previewers past the country challenge without
bypassing managed WAF. Idempotent by description.
"""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.push_validator_allowlist")

RULE_DESCRIPTION = "NKP: validator + crawler allowlist"

USER_AGENTS = [
    # Validators + performance tools
    "Google-InspectionTool",
    "Schema-Markup-Validator",
    "Chrome-Lighthouse",
    "PageSpeed",
    # Social media previewers
    "facebookexternalhit",
    "LinkedInBot",
    "Slackbot-LinkExpanding",
    "Twitterbot",
    "Discordbot",
    # AI search crawlers (GEO — Generative Engine Optimization)
    "ClaudeBot",
    "anthropic-ai",
    "ChatGPT-User",
    "GPTBot",
    "PerplexityBot",
    "Google-Extended",
]

RULE_EXPRESSION = " or ".join(
    f'(http.user_agent contains "{ua}")' for ua in USER_AGENTS
)

# Narrow skip scope: custom rules + SBFM only. Managed WAF stays active.
RULE_BODY = {
    "action": "skip",
    "action_parameters": {
        "ruleset": "current",
        "phases": ["http_request_sbfm"],
    },
    "expression": RULE_EXPRESSION,
    "description": RULE_DESCRIPTION,
    "enabled": True,
}

VALID_MODES = {"apply", "remove", "dry_run"}

CHANGELOG_DIR = Path("data/reports")
VERIFY_UA = "Mozilla/5.0 (compatible; Schema-Markup-Validator/1.0; +https://validator.schema.org/)"


class PushValidatorAllowlistSkill(BaseSkill):
    name = "cloudflare.push_validator_allowlist"
    description = (
        "Push/remove the validator + crawler allowlist Skip rule on a CF zone. "
        "Idempotent. Writes JSONL changelog."
    )
    required_inputs = ["target"]
    optional_inputs = ["mode", "verify", "changelog"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        mode = kwargs.get("mode", "dry_run")
        verify = kwargs.get("verify", True)
        changelog = kwargs.get("changelog", True)

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
        existing_rules = ruleset.get("rules", [])
        existing_allowlist = next(
            (r for r in existing_rules if r.get("description") == RULE_DESCRIPTION),
            None,
        )

        base_data = {
            "domain": target,
            "zone_id": zone_id,
            "ruleset_id": ruleset_id,
            "existing_rules_count": len(existing_rules),
            "already_present": existing_allowlist is not None,
        }

        if mode == "dry_run":
            base_data["action_taken"] = "none"
            base_data["would_add"] = existing_allowlist is None
            base_data["would_remove"] = existing_allowlist is not None
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: dry run — "
                + ("already present" if existing_allowlist else "would add"),
            )

        if mode == "remove":
            if existing_allowlist is None:
                base_data["action_taken"] = "not_present"
                return SkillResult(
                    status=SkillStatus.SUCCESS,
                    data=base_data,
                    message=f"{target}: rule not present, nothing to remove",
                )
            try:
                await client.delete(
                    f"/zones/{zone_id}/rulesets/{ruleset_id}/rules/{existing_allowlist['id']}"
                )
            except APIError as e:
                return SkillResult(
                    status=SkillStatus.FAILURE,
                    message=f"{target}: delete failed — {e}",
                    errors=[str(e)],
                )
            base_data["action_taken"] = "removed"
            base_data["removed_rule_id"] = existing_allowlist["id"]
            if changelog:
                _write_changelog(base_data, "removed")
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: allowlist rule removed",
            )

        # mode == "apply"
        if existing_allowlist is not None:
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

        new_rules = resp.get("result", {}).get("rules", [])
        added_rule = next(
            (r for r in new_rules if r.get("description") == RULE_DESCRIPTION),
            None,
        )
        base_data["action_taken"] = "added"
        base_data["new_rule_id"] = added_rule.get("id") if added_rule else None
        base_data["rules_after"] = len(new_rules)

        if verify:
            verify_result = await _verify_with_curl(target)
            base_data["verification"] = verify_result
            if not verify_result.get("ok"):
                if changelog:
                    _write_changelog(base_data, "added_but_verify_failed")
                return SkillResult(
                    status=SkillStatus.WARNING,
                    data=base_data,
                    message=f"{target}: rule added but verification failed: {verify_result.get('reason')}",
                )

        if changelog:
            _write_changelog(base_data, "added")

        return SkillResult(
            status=SkillStatus.SUCCESS,
            data=base_data,
            message=f"{target}: allowlist rule added"
            + (" + verified" if verify else ""),
        )


VERIFY_RETRY_DELAYS = (5, 10, 15)  # seconds; CF rule propagation can take 5-30s


async def _verify_with_curl(domain: str) -> dict:
    """GET homepage with a validator UA. Retries with backoff for CF propagation."""
    url = f"https://www.{domain}/" if not domain.startswith("www.") else f"https://{domain}/"
    last_resp = None
    for attempt, delay in enumerate(VERIFY_RETRY_DELAYS, start=1):
        await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
                resp = await http.get(url, headers={"User-Agent": VERIFY_UA})
            if resp.status_code == 200:
                return {
                    "ok": True,
                    "status_code": 200,
                    "url": str(resp.url),
                    "attempts": attempt,
                }
            last_resp = resp
        except httpx.HTTPError as e:
            last_resp = e
    if isinstance(last_resp, httpx.Response):
        return {
            "ok": False,
            "status_code": last_resp.status_code,
            "reason": f"got {last_resp.status_code} after {len(VERIFY_RETRY_DELAYS)} attempts (expected 200)",
            "cf_mitigated": last_resp.headers.get("cf-mitigated"),
            "attempts": len(VERIFY_RETRY_DELAYS),
        }
    return {
        "ok": False,
        "reason": f"network error after {len(VERIFY_RETRY_DELAYS)} attempts: {last_resp}",
        "attempts": len(VERIFY_RETRY_DELAYS),
    }


def _write_changelog(data: dict, action: str) -> None:
    """Append a JSONL entry to data/reports/cf-rule-changes-YYYY-MM-DD.jsonl."""
    now = datetime.now(timezone.utc)
    path = CHANGELOG_DIR / f"cf-rule-changes-{now.strftime('%Y-%m-%d')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "skill": "cloudflare.push_validator_allowlist",
        "domain": data["domain"],
        "zone_id": data["zone_id"],
        "ruleset_id": data["ruleset_id"],
        "action": action,
        "rule_description": RULE_DESCRIPTION,
        "rule_id": data.get("new_rule_id") or data.get("removed_rule_id"),
        "verification": data.get("verification"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
