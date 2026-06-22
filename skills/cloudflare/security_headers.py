from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.security_headers")

RECOMMENDED_HEADERS = [
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Content-Security-Policy",
]


def _extract_headers_from_ruleset(ruleset: dict) -> set[str]:
    """Extract all header names set by transform rules."""
    found = set()
    for rule in ruleset.get("rules", []):
        action_params = rule.get("action_parameters", {})
        headers = action_params.get("headers", {})
        found.update(headers.keys())
    return found


class SecurityHeadersSkill(BaseSkill):
    name = "cloudflare.security_headers"
    description = "Check HSTS and security response headers via Cloudflare transform rules"
    required_inputs = ["target"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)

            # Check HSTS via zone settings
            settings = await client.get_zone_settings(zone_id)
            security_header = settings.get("security_header", {})
            hsts = security_header.get("strict_transport_security", {}) if isinstance(security_header, dict) else {}
            hsts_enabled = hsts.get("enabled", False)
            hsts_max_age = hsts.get("max_age", 0)
            hsts_nosniff = hsts.get("nosniff", False)

            # Check transform rules for security headers
            ruleset = await client.get_ruleset(zone_id, "http_response_headers_transform")

            issues = []

            if not hsts_enabled:
                issues.append("HSTS is disabled")

            present_headers = set()
            if ruleset:
                present_headers = _extract_headers_from_ruleset(ruleset)
            else:
                issues.append("No HTTP response header transform ruleset found")

            missing_headers = [h for h in RECOMMENDED_HEADERS if h not in present_headers]
            if missing_headers:
                issues.append(f"Missing recommended headers: {', '.join(missing_headers)}")

            status = SkillStatus.SUCCESS if not issues else SkillStatus.WARNING
            return SkillResult(
                status=status,
                data={
                    "domain": target,
                    "hsts_enabled": hsts_enabled,
                    "hsts_max_age": hsts_max_age,
                    "hsts_nosniff": hsts_nosniff,
                    "present_headers": sorted(present_headers),
                    "missing_headers": missing_headers,
                    "issues": issues,
                },
                message=f"{target}: security headers {'OK' if not issues else '; '.join(issues)}",
            )

        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Security headers check failed for {target}: {e}",
                errors=[str(e)],
            )
