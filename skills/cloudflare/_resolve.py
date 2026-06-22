# skills/cloudflare/_resolve.py
"""Shared target resolution: domain/account/'all' → list of {domain, zone_id}."""

from pathlib import Path
from core.config import NKPConfig
from core.logger import get_logger

log = get_logger("cloudflare.resolve")


async def resolve_targets(
    target: str,
    client,  # CloudflareClient — not typed to avoid circular import
    root_dir: Path | None = None,
) -> list[dict]:
    """
    Resolve a target string to a list of {domain, zone_id} dicts.

    target can be:
    - A domain (contains '.'): resolve to single zone
    - An account name (e.g. 'acctC'): look up all active domains for that account
    - 'all': all active domains from sites.json
    """
    config = NKPConfig(root_dir=root_dir)

    if target == "all":
        domains = [s["domain"] for s in config.get_active_sites()]
    elif "." in target:
        # Looks like a domain
        zone_id = await client.get_zone_id(target)
        return [{"domain": target, "zone_id": zone_id}]
    else:
        # Assume account name
        sites = config.get_sites_by_account(target)
        domains = [s["domain"] for s in sites]

    if not domains:
        log.warning(f"No domains found for target: {target}")
        return []

    results = []
    errors = []
    for domain in domains:
        try:
            zone_id = await client.get_zone_id(domain)
            results.append({"domain": domain, "zone_id": zone_id})
        except Exception as e:
            log.warning(f"Could not resolve zone for {domain}: {e}")
            errors.append({"domain": domain, "error": str(e)})

    if errors:
        log.warning(f"Failed to resolve {len(errors)}/{len(domains)} domains")

    return results
