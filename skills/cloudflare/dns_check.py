from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare.client import get_cf_client
from core.errors import APIError
from core.logger import get_logger

log = get_logger("cloudflare.dns_check")

EXPECTED_CNAME_TARGET = "wp.wpenginepowered.com"


class DnsCheckSkill(BaseSkill):
    name = "cloudflare.dns_check"
    description = "Verify DNS records for O2O — CNAME target, proxy status"
    required_inputs = ["target"]

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]

        try:
            client = get_cf_client()
            zone_id = await client.get_zone_id(target)
            data = await client.get(f"/zones/{zone_id}/dns_records")
            records = data.get("result", [])

            issues = []
            record_list = []
            www_found = False

            for rec in records:
                name = rec.get("name", "")
                rtype = rec.get("type", "")
                content = rec.get("content", "")
                proxied = rec.get("proxied", False)

                is_www = name.startswith("www.") or name == f"www.{target}"
                ok = True

                if is_www:
                    www_found = True
                    if rtype != "CNAME":
                        issues.append(f"www record is {rtype}, expected CNAME")
                        ok = False
                    if content != EXPECTED_CNAME_TARGET:
                        issues.append(f"www CNAME points to {content}, expected {EXPECTED_CNAME_TARGET}")
                        ok = False
                    if not proxied:
                        issues.append("www record is not proxied (grey cloud)")
                        ok = False

                record_list.append({
                    "name": name,
                    "type": rtype,
                    "content": content,
                    "proxied": proxied,
                    "ok": ok,
                })

            if not www_found:
                issues.append("No www record found")

            status = SkillStatus.SUCCESS if not issues else SkillStatus.WARNING
            return SkillResult(
                status=status,
                data={
                    "domain": target,
                    "zone_id": zone_id,
                    "records": record_list,
                    "issues": issues,
                },
                message=f"{target}: {len(issues)} DNS issues" if issues else f"{target}: DNS OK",
            )

        except APIError as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"DNS check failed for {target}: {e}",
                errors=[str(e)],
            )
