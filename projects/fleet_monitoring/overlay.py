"""Load fixed-sites.yml and turn it into an apex-keyed overlay index.

fixed-sites.yml keeps its exact format and role. It is NOT the roster — it is an
overlay marking the managed/fixed subset with baselines and skip flags.
"""
from __future__ import annotations
import yaml

from .models import OVERLAY_FILE


def load_tracker() -> dict:
    """Read fixed-sites.yml. Returns {} if absent."""
    if not OVERLAY_FILE.exists():
        return {}
    return yaml.safe_load(OVERLAY_FILE.read_text(encoding="utf-8")) or {}


def build_overlay_index(tracker: dict) -> dict[str, dict]:
    """apex -> {fixed, fix_date, pre_fix_bandwidth_gb_30d, pre_fix_mb_per_visit,
    skip_country_challenge, fixes, notes}."""
    idx: dict[str, dict] = {}
    for site in tracker.get("sites", []) or []:
        apex = (site.get("apex") or "").strip().lower()
        if not apex:
            continue
        idx[apex] = {
            "fixed": True,
            "fix_date": site.get("fix_date"),
            "pre_fix_bandwidth_gb_30d": site.get("pre_fix_bandwidth_gb_30d"),
            "pre_fix_mb_per_visit": site.get("pre_fix_mb_per_visit"),
            "skip_country_challenge": site.get("skip_country_challenge", False),
            "fixes": site.get("fixes", []),
        }
    return idx
