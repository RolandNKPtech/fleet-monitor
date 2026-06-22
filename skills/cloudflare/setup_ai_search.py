"""Composer skill: AI search bot setup for a CF zone.

The "AI search project" pattern. Four steps in order:
  1. Bootstrap empty http_request_firewall_custom ruleset (no-op if already exists)
  2. Push validator + AI search crawler allowlist (skill: push_validator_allowlist)
  3. Push NKP internal-scanner allowlist (skill: push_scanner_allowlist) so our
     fleet scans don't get blocked by country challenge / bot protection
  4. Enable Block AI Scrapers (ai_bots_protection=block) + Block Crawlers (crawler_protection=enabled)

Result: known AI search bots (ClaudeBot, GPTBot, PerplexityBot, Google-Extended,
anthropic-ai, ChatGPT-User) get clean access to crawl + cite the site for AI
Overviews / ChatGPT search / Perplexity / Claude. Unknown / non-allowlisted AI
scrapers get blocked, saving bandwidth.

Use this on every active client zone where we want AI search citations AND
bandwidth control. Safe to run on its own (no country challenge, no full O2O).

Idempotent. Reports each step's action.
"""
from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from skills.cloudflare.push_validator_allowlist import PushValidatorAllowlistSkill
from skills.cloudflare.push_scanner_allowlist import PushScannerAllowlistSkill
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.setup_ai_search")

VALID_MODES = {"apply", "dry_run"}


class SetupAiSearchSkill(BaseSkill):
    name = "cloudflare.setup_ai_search"
    description = (
        "AI search project composer: bootstrap WAF ruleset + push validator/AI "
        "allowlist + enable Block AI Scrapers & Crawlers. Idempotent."
    )
    required_inputs = ["target"]
    optional_inputs = ["mode", "changelog"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]
        mode = kwargs.get("mode", "dry_run")
        changelog = kwargs.get("changelog", True)

        if mode not in VALID_MODES:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Invalid mode '{mode}'. Must be one of {sorted(VALID_MODES)}",
                errors=[f"mode must be one of {sorted(VALID_MODES)}"],
            )

        client = get_cf_client()
        try:
            zone_id = await client.get_zone_id(target)
        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: zone lookup failed — {e}",
                errors=[str(e)],
            )

        # Step 1: bootstrap empty http_request_firewall_custom ruleset if missing
        bootstrap_action = "skipped"
        try:
            existing = await client.get_ruleset(zone_id, "http_request_firewall_custom")
        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: ruleset read failed — {e}",
                errors=[str(e)],
            )

        if existing is None:
            if mode == "dry_run":
                bootstrap_action = "would_create"
            else:
                try:
                    await client._request(
                        "PUT",
                        f"/zones/{zone_id}/rulesets/phases/http_request_firewall_custom/entrypoint",
                        json={"rules": []},
                    )
                    bootstrap_action = "created"
                except APIError as e:
                    return SkillResult(
                        status=SkillStatus.FAILURE,
                        message=f"{target}: ruleset bootstrap failed — {e}",
                        errors=[str(e)],
                    )
        else:
            bootstrap_action = "already_exists"

        # Step 2: validator + AI allowlist
        allowlist_skill = PushValidatorAllowlistSkill()
        allowlist_result = await allowlist_skill.run(
            target=target, mode=mode, changelog=changelog
        )
        if allowlist_result.status == SkillStatus.FAILURE:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: allowlist step failed — {allowlist_result.message}",
                data={
                    "bootstrap": bootstrap_action,
                    "allowlist": allowlist_result.data,
                    "ai_bot_block": None,
                },
                errors=allowlist_result.errors,
            )

        # Step 2.5: NKP internal-scanner allowlist (so our fleet scans don't
        # get country-challenged / WAF-blocked on our own zones).
        scanner_skill = PushScannerAllowlistSkill()
        scanner_result = await scanner_skill.run(target=target, mode=mode)
        if scanner_result.status == SkillStatus.FAILURE:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"{target}: scanner allowlist step failed — {scanner_result.message}",
                data={
                    "bootstrap": bootstrap_action,
                    "allowlist": allowlist_result.data,
                    "scanner_allowlist": scanner_result.data,
                    "ai_bot_block": None,
                },
                errors=scanner_result.errors,
            )

        # Step 3: Block AI Scrapers + Crawlers via bot_management PUT.
        # Notes: ai_bots_protection accepts {block,disabled}; crawler_protection
        # accepts {enabled,disabled}. The PUT must include enable_js + fight_mode
        # or CF returns 400 "invalid Super Bot Fight Mode settings".
        bot_block_action = "n/a"
        bm_after = None
        if mode == "dry_run":
            try:
                bm = await client.get(f"/zones/{zone_id}/bot_management")
                cur = bm.get("result", {}) or {}
                ai = cur.get("ai_bots_protection")
                cr = cur.get("crawler_protection")
                already = ai == "block" and cr == "enabled"
                bot_block_action = "already_enabled" if already else "would_enable"
                bm_after = cur
            except APIError as e:
                return SkillResult(
                    status=SkillStatus.FAILURE,
                    message=f"{target}: bot_management read failed — {e}",
                    errors=[str(e)],
                )
        else:
            try:
                bm = await client.get(f"/zones/{zone_id}/bot_management")
                cur = bm.get("result", {}) or {}
                body = {
                    "ai_bots_protection": "block",
                    "crawler_protection": "enabled",
                    "enable_js": cur.get("enable_js", False),
                    "fight_mode": cur.get("fight_mode", False),
                }
                resp = await client._request(
                    "PUT", f"/zones/{zone_id}/bot_management", json=body
                )
                bm_after = resp.get("result") or cur
                bot_block_action = (
                    "already_enabled"
                    if cur.get("ai_bots_protection") == "block"
                    and cur.get("crawler_protection") == "enabled"
                    else "enabled"
                )
            except APIError as e:
                return SkillResult(
                    status=SkillStatus.FAILURE,
                    message=f"{target}: bot_management update failed — {e}",
                    errors=[str(e)],
                )

        return SkillResult(
            status=SkillStatus.SUCCESS,
            data={
                "domain": target,
                "zone_id": zone_id,
                "mode": mode,
                "bootstrap": bootstrap_action,
                "allowlist": allowlist_result.data,
                "scanner_allowlist": scanner_result.data,
                "ai_bot_block": bot_block_action,
                "bot_management": bm_after,
            },
            message=(
                f"{target}: AI search setup ({mode}) — "
                f"bootstrap={bootstrap_action}, "
                f"allowlist={(allowlist_result.data or {}).get('action_taken', 'unknown')}, "
                f"scanner={(scanner_result.data or {}).get('action_taken', 'unknown')}, "
                f"ai_bot_block={bot_block_action}"
            ),
        )
