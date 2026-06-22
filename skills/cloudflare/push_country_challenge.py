"""Push the standard NKP country challenge rule to a CF zone's custom WAF ruleset.

Standard expression: (ip.src.country ne "US" and not cf.client.bot)
Action: managed_challenge

Idempotent by description. Source of truth: data/standards/cf-config.yml o2o_full.
Used by full O2O setup as the noise-filter for non-US non-verified-bot traffic.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from skills.cloudflare.check_traffic_geography import CheckTrafficGeographySkill
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.push_country_challenge")

RULE_DESCRIPTION = "Challenge Non-US Traffic"
STANDARD_EXPRESSION = '(ip.src.country ne "US" and not cf.client.bot)'

RULE_BODY = {
    "action": "managed_challenge",
    "expression": STANDARD_EXPRESSION,
    "description": RULE_DESCRIPTION,
    "enabled": True,
}

VALID_MODES = {"apply", "remove", "dry_run"}
CHANGELOG_DIR = Path("data/reports")


class PushCountryChallengeSkill(BaseSkill):
    name = "cloudflare.push_country_challenge"
    description = (
        "Push/remove the standard country challenge rule on a CF zone. "
        "Idempotent. Writes JSONL changelog. Required for full O2O setup."
    )
    required_inputs = ["target"]
    optional_inputs = ["mode", "changelog", "pre_check_geography", "min_us_pct", "geography_days"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        mode = kwargs.get("mode", "dry_run")
        changelog = kwargs.get("changelog", True)
        pre_check_geography = kwargs.get("pre_check_geography", True)
        min_us_pct = float(kwargs.get("min_us_pct") or 90.0)
        geography_days = int(kwargs.get("geography_days") or 7)

        if mode not in VALID_MODES:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Invalid mode '{mode}'. Must be one of {sorted(VALID_MODES)}",
                errors=[f"mode must be one of {sorted(VALID_MODES)}"],
            )

        # Pre-check: don't push challenge to sites with significant non-US traffic.
        # Only runs on apply; skipped for dry_run (caller can request the check separately)
        # and remove (we should always be able to undo).
        geography_data = None
        if pre_check_geography and mode == "apply":
            geo_skill = CheckTrafficGeographySkill()
            geo_result = await geo_skill.run(
                target=target, days=geography_days, min_us_pct=min_us_pct
            )
            geography_data = geo_result.data
            if geo_result.status == SkillStatus.FAILURE:
                return SkillResult(
                    status=SkillStatus.FAILURE,
                    message=f"{target}: pre-check (geography) failed — {geo_result.message}",
                    data={"geography": geography_data},
                    errors=geo_result.errors,
                )
            if not (geo_result.data or {}).get("is_us_dominant"):
                pct = (geo_result.data or {}).get("us_pct")
                pct_str = f"{pct}%" if pct is not None else "no data"
                return SkillResult(
                    status=SkillStatus.SKIPPED,
                    data={
                        "domain": target,
                        "action_taken": "skipped_geography",
                        "geography": geography_data,
                    },
                    message=(
                        f"{target}: SKIPPED — site is not US-dominant "
                        f"({pct_str} US over {geography_days}d, threshold {min_us_pct}%). "
                        f"Country challenge would harm international visitors."
                    ),
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
        existing_country = next(
            (
                r for r in existing_rules
                if r.get("description") == RULE_DESCRIPTION
                or r.get("expression") == STANDARD_EXPRESSION
            ),
            None,
        )

        base_data = {
            "domain": target,
            "zone_id": zone_id,
            "ruleset_id": ruleset_id,
            "existing_rules_count": len(existing_rules),
            "already_present": existing_country is not None,
            "geography": geography_data,
        }

        if mode == "dry_run":
            base_data["action_taken"] = "none"
            base_data["would_add"] = existing_country is None
            base_data["would_remove"] = existing_country is not None
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: dry run — "
                + ("already present" if existing_country else "would add"),
            )

        if mode == "remove":
            if existing_country is None:
                base_data["action_taken"] = "not_present"
                return SkillResult(
                    status=SkillStatus.SUCCESS,
                    data=base_data,
                    message=f"{target}: country challenge not present, nothing to remove",
                )
            try:
                await client.delete(
                    f"/zones/{zone_id}/rulesets/{ruleset_id}/rules/{existing_country['id']}"
                )
            except APIError as e:
                return SkillResult(
                    status=SkillStatus.FAILURE,
                    message=f"{target}: delete failed — {e}",
                    errors=[str(e)],
                )
            base_data["action_taken"] = "removed"
            base_data["removed_rule_id"] = existing_country["id"]
            if changelog:
                _write_changelog(base_data, "removed")
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: country challenge removed",
            )

        # mode == "apply"
        if existing_country is not None:
            base_data["action_taken"] = "already_present"
            return SkillResult(
                status=SkillStatus.SUCCESS,
                data=base_data,
                message=f"{target}: country challenge already present",
            )

        try:
            resp = await client.post(
                f"/zones/{zone_id}/rulesets/{ruleset_id}/rules",
                json=RULE_BODY,
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

        if changelog:
            _write_changelog(base_data, "added")

        return SkillResult(
            status=SkillStatus.SUCCESS,
            data=base_data,
            message=f"{target}: country challenge added",
        )


def _write_changelog(data: dict, action: str) -> None:
    now = datetime.now(timezone.utc)
    path = CHANGELOG_DIR / f"cf-rule-changes-{now.strftime('%Y-%m-%d')}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "skill": "cloudflare.push_country_challenge",
        "domain": data["domain"],
        "zone_id": data["zone_id"],
        "ruleset_id": data["ruleset_id"],
        "action": action,
        "rule_description": RULE_DESCRIPTION,
        "rule_id": data.get("new_rule_id") or data.get("removed_rule_id"),
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
