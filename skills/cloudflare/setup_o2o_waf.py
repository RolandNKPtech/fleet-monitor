"""Composer skill: complete WAF setup for a full O2O zone.

Pushes (in order, idempotent):
  1. cloudflare.push_country_challenge — country challenge rule
  2. cloudflare.push_validator_allowlist — validator/crawler allowlist (sits before challenge)

Then audits with cloudflare.check_waf to verify the result.

Use this as the single entry point for any pipeline that needs a full O2O WAF
configuration. Future scripts/o2o_pipeline.py should call this skill rather than
duplicating the rule-push logic.
"""
from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.push_country_challenge import PushCountryChallengeSkill
from skills.cloudflare.push_validator_allowlist import PushValidatorAllowlistSkill
from skills.cloudflare.check_waf import CheckWafSkill
from core.logger import get_logger

log = get_logger("cloudflare.setup_o2o_waf")

VALID_MODES = {"apply", "dry_run"}


class SetupO2oWafSkill(BaseSkill):
    name = "cloudflare.setup_o2o_waf"
    description = (
        "Full O2O WAF setup composer: country challenge + validator allowlist + audit. "
        "Idempotent. Use as entry point from any O2O setup pipeline."
    )
    required_inputs = ["target"]
    optional_inputs = [
        "mode",
        "verify",
        "changelog",
        "pre_check_geography",
        "min_us_pct",
        "geography_days",
    ]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        mode = kwargs.get("mode", "dry_run")
        verify = kwargs.get("verify", True)
        changelog = kwargs.get("changelog", True)
        pre_check_geography = kwargs.get("pre_check_geography", True)
        min_us_pct = kwargs.get("min_us_pct")
        geography_days = kwargs.get("geography_days")

        if mode not in VALID_MODES:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Invalid mode '{mode}'. Must be one of {sorted(VALID_MODES)}",
                errors=[f"mode must be one of {sorted(VALID_MODES)}"],
            )

        # Step 1: country challenge (with geography pre-check). If pre-check says
        # the site is international, the country skill returns SKIPPED; we honor that
        # and STILL push the validator allowlist (it's harmless and useful for any
        # site that has the country challenge later, but here just no-ops since
        # there's no challenge to bypass).
        country_skill = PushCountryChallengeSkill()
        country_kwargs = {
            "target": target,
            "mode": mode,
            "changelog": changelog,
            "pre_check_geography": pre_check_geography,
        }
        if min_us_pct is not None:
            country_kwargs["min_us_pct"] = min_us_pct
        if geography_days is not None:
            country_kwargs["geography_days"] = geography_days
        country_result = await country_skill.run(**country_kwargs)

        if country_result.status == SkillStatus.FAILURE:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: country challenge step failed — {country_result.message}",
                data={"country_challenge": country_result.data, "validator_allowlist": None, "audit": None},
                errors=country_result.errors,
            )

        # Step 2: validator allowlist (run regardless of country challenge skip —
        # allowlist is harmless on its own and protects validators if challenge gets
        # added later)
        allowlist_skill = PushValidatorAllowlistSkill()
        allowlist_result = await allowlist_skill.run(
            target=target, mode=mode, verify=verify, changelog=changelog
        )

        # Step 3: audit (always run, even on dry_run, so caller sees current posture)
        audit_skill = CheckWafSkill()
        audit_result = await audit_skill.run(target=target)

        combined_status = _combine_status(country_result.status, allowlist_result.status, audit_result.status)

        return SkillResult(
            status=combined_status,
            data={
                "domain": target,
                "mode": mode,
                "country_challenge": country_result.data,
                "validator_allowlist": allowlist_result.data,
                "audit": audit_result.data,
            },
            message=(
                f"{target}: O2O WAF setup ({mode}) — "
                f"challenge={country_result.data.get('action_taken') if country_result.data else 'n/a'}, "
                f"allowlist={allowlist_result.data.get('action_taken') if allowlist_result.data else 'n/a'}, "
                f"audit_issues={len(audit_result.data.get('issues', [])) if audit_result.data else 'n/a'}"
            ),
        )


def _combine_status(*statuses: SkillStatus) -> SkillStatus:
    """FAILURE > WARNING > SKIPPED > SUCCESS. Highest severity wins.
    SKIPPED bubbles up because the WAF stack ends up incomplete by design."""
    if any(s == SkillStatus.FAILURE for s in statuses):
        return SkillStatus.FAILURE
    if any(s == SkillStatus.WARNING for s in statuses):
        return SkillStatus.WARNING
    if any(s == SkillStatus.SKIPPED for s in statuses):
        return SkillStatus.SKIPPED
    return SkillStatus.SUCCESS
