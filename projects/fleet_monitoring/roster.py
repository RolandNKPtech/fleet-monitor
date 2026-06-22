"""Fleet roster discovery: enumerate WPE installs + CF zones, join on apex domain.

The roster is the universe of sites. fixed-sites.yml is an overlay applied later.
"""
from __future__ import annotations
import json
import sys

from .models import ROSTER_FILE


def _apex_from_domain(domain: str | None) -> str | None:
    """www.example-clinic.com -> example-clinic.com. Leaves bare apex untouched."""
    if not domain:
        return None
    d = domain.strip().lower()
    return d[4:] if d.startswith("www.") else d


def _zone_plan(zone: dict | None) -> dict | None:
    """Extract the billing-relevant plan fields from a CF zone response.

    Returned shape: {name, price, currency, frequency} or None. We strip
    out the volatile fields (id, externally_managed) that would otherwise
    cause spurious drift; name + price are what an operator cares about
    for cost reporting and plan-change alerts.
    """
    if not zone:
        return None
    plan = zone.get("plan") or {}
    if not plan:
        return None
    return {
        "name": plan.get("name"),
        "price": plan.get("price"),
        "currency": plan.get("currency") or "USD",
        "frequency": plan.get("frequency") or "monthly",
    }


def join_roster(installs: list[dict], zones: list[dict]) -> list[dict]:
    """Join WPE installs and CF zones on apex domain.

    Returns [{key, apex, join_state, wpe_install, wpe_account, wpe_install_id,
              cf_zone_id, cf_plan}, ...]. key == apex domain.
    join_state: wpe+cf | wpe-only | cf-only

    When two installs share the same apex (e.g. a prod + a staging install both
    reporting the same primary_domain) the later one wins — the collision is
    printed to stderr so the operator can see it.
    """
    zone_by_name = {z["name"].lower(): z for z in zones}
    seen: dict[str, dict] = {}

    for inst in installs:
        apex = _apex_from_domain(inst.get("primary_domain")) or inst["name"]
        if apex in seen:
            prior = seen[apex]["wpe_install"]
            print(f"  WARN roster: apex {apex!r} claimed by both "
                  f"{prior!r} and {inst['name']!r}; keeping the latter",
                  file=sys.stderr)
        zone = zone_by_name.get(apex)
        seen[apex] = {
            "key": apex,
            "apex": apex,
            "join_state": "wpe+cf" if zone else "wpe-only",
            "wpe_install": inst["name"],
            "wpe_account": inst.get("account_id"),
            "wpe_install_id": inst.get("id"),
            "cf_zone_id": zone["id"] if zone else None,
            "cf_plan": _zone_plan(zone),
        }

    for zone in zones:
        apex = zone["name"].lower()
        if apex in seen:
            continue
        seen[apex] = {
            "key": apex,
            "apex": apex,
            "join_state": "cf-only",
            "wpe_install": None,
            "wpe_account": None,
            "wpe_install_id": None,
            "cf_zone_id": zone["id"],
            "cf_plan": _zone_plan(zone),
        }
    return sorted(seen.values(), key=lambda s: s["key"])


def roster_diff(old: list[dict], new: list[dict]) -> dict:
    """Compare two rosters by key. Returns {added: [...], removed: [...]}."""
    old_keys = {s["key"] for s in old}
    new_keys = {s["key"] for s in new}
    return {
        "added": sorted(new_keys - old_keys),
        "removed": sorted(old_keys - new_keys),
    }


def load_previous_roster() -> list[dict]:
    """Read data/roster.json from the last run. Returns [] if absent."""
    if not ROSTER_FILE.exists():
        return []
    return json.loads(ROSTER_FILE.read_text(encoding="utf-8"))


def save_roster(roster: list[dict]) -> None:
    ROSTER_FILE.parent.mkdir(parents=True, exist_ok=True)
    ROSTER_FILE.write_text(json.dumps(roster, indent=2), encoding="utf-8")
