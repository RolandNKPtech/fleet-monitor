from pathlib import Path

import yaml

from core.errors import APIError
from core.logger import get_logger
from skills.base import BaseSkill, SkillResult, SkillStatus
from skills.cloudflare._resolve import resolve_targets
from skills.cloudflare.client import get_cf_client

log = get_logger("cloudflare.audit_config")

# Severity map: setting_id -> severity level
SEVERITY = {
    "ssl": "critical",
    "automatic_platform_optimization": "critical",
    "security_header": "high",
    "rocket_loader": "high",
    "early_hints": "medium",
    "minify": "medium",
    "always_use_https": "medium",
    "smart_tiered_cache": "medium",
}

_DEFAULT_STANDARDS_PATH = Path("data/standards/cf-config.yml")


def _load_standards(standards_path: Path | None = None) -> dict:
    """Load o2o_base standards from cf-config.yml."""
    path = standards_path or _DEFAULT_STANDARDS_PATH
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("o2o_base", {})


def _compare_setting(setting_id: str, actual, expected) -> list[dict]:
    """
    Compare actual vs expected for a setting.
    Returns list of drift items (empty if compliant).
    """
    drifts = []

    if setting_id == "security_header":
        # Compare strict_transport_security.enabled and .max_age
        actual_hsts = actual.get("strict_transport_security", {}) if isinstance(actual, dict) else {}
        exp_hsts = expected if isinstance(expected, dict) else {}
        if actual_hsts.get("enabled") != exp_hsts.get("enabled"):
            drifts.append({
                "setting": setting_id,
                "field": "strict_transport_security.enabled",
                "expected": exp_hsts.get("enabled"),
                "actual": actual_hsts.get("enabled"),
                "severity": SEVERITY.get(setting_id, "medium"),
            })
        if actual_hsts.get("max_age") != exp_hsts.get("max_age"):
            drifts.append({
                "setting": setting_id,
                "field": "strict_transport_security.max_age",
                "expected": exp_hsts.get("max_age"),
                "actual": actual_hsts.get("max_age"),
                "severity": SEVERITY.get(setting_id, "medium"),
            })

    elif setting_id == "automatic_platform_optimization":
        actual_enabled = actual.get("enabled") if isinstance(actual, dict) else actual
        exp_enabled = expected.get("enabled") if isinstance(expected, dict) else expected
        if actual_enabled != exp_enabled:
            drifts.append({
                "setting": setting_id,
                "field": "enabled",
                "expected": exp_enabled,
                "actual": actual_enabled,
                "severity": SEVERITY.get(setting_id, "medium"),
            })

    elif setting_id == "minify":
        actual_m = actual if isinstance(actual, dict) else {}
        exp_m = expected if isinstance(expected, dict) else {}
        for sub in ("css", "html", "js"):
            if actual_m.get(sub) != exp_m.get(sub):
                drifts.append({
                    "setting": setting_id,
                    "field": sub,
                    "expected": exp_m.get(sub),
                    "actual": actual_m.get(sub),
                    "severity": SEVERITY.get(setting_id, "medium"),
                })

    else:
        if actual != expected:
            drifts.append({
                "setting": setting_id,
                "expected": expected,
                "actual": actual,
                "severity": SEVERITY.get(setting_id, "medium"),
            })

    return drifts


def _audit_zone_settings(settings: dict, standards: dict) -> list[dict]:
    """Compare zone settings against standards, return list of drift items."""
    drift = []
    for setting_id, expected in standards.items():
        # Skip non-setting keys (e.g. cache_rule_required)
        if setting_id not in settings:
            continue
        actual = settings[setting_id]
        drift.extend(_compare_setting(setting_id, actual, expected))
    return drift


class AuditConfigSkill(BaseSkill):
    name = "cloudflare.audit_config"
    description = "Audit Cloudflare zone settings against O2O standards, report drift with severity"
    required_inputs = ["target"]

    def __init__(self, standards_path: Path | None = None, root_dir: Path | None = None):
        self._standards_path = standards_path
        self._root_dir = root_dir

    async def run(self, **kwargs) -> SkillResult:
        await self.validate_inputs(**kwargs)
        target = kwargs["target"]

        try:
            client = get_cf_client()
            standards = _load_standards(self._standards_path)
            zones = await resolve_targets(target, client, root_dir=self._root_dir)

            if not zones:
                return SkillResult(
                    status=SkillStatus.FAILURE,
                    message=f"No zones resolved for target: {target}",
                    errors=[f"No zones found for {target}"],
                )

        except Exception as e:
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"Audit config failed: {e}",
                errors=[str(e)],
            )

        all_drift = []
        zone_results = []
        errors = []

        for zone in zones:
            domain = zone["domain"]
            zone_id = zone["zone_id"]
            try:
                settings = await client.get_zone_settings(zone_id)
                drift = _audit_zone_settings(settings, standards)
                zone_results.append({
                    "domain": domain,
                    "zone_id": zone_id,
                    "compliant": len(drift) == 0,
                    "drift": drift,
                })
                all_drift.extend(drift)
            except APIError as e:
                log.warning(f"Zone API call failed for {domain} ({zone_id}): {e}")
                errors.append({"domain": domain, "zone_id": zone_id, "error": str(e)})

        # Determine status
        if not zone_results and errors:
            # All zones failed
            return SkillResult(
                status=SkillStatus.FAILURE,
                message=f"All zone API calls failed for target: {target}",
                errors=[e["error"] for e in errors],
            )

        has_drift = len(all_drift) > 0
        has_errors = len(errors) > 0
        if has_drift or has_errors:
            status = SkillStatus.WARNING
            msg_parts = []
            if has_drift:
                msg_parts.append(f"{len(all_drift)} drift item(s)")
            if has_errors:
                msg_parts.append(f"{len(errors)} zone error(s)")
            message = f"{target}: " + ", ".join(msg_parts)
        else:
            status = SkillStatus.SUCCESS
            message = f"{target}: all settings compliant"

        return SkillResult(
            status=status,
            data={
                "target": target,
                "zones_audited": len(zone_results),
                "zones_errored": len(errors),
                "compliant": all(z["compliant"] for z in zone_results),
                "drift": all_drift,
                "zone_results": zone_results,
                "errors": errors,
            },
            message=message,
        )
