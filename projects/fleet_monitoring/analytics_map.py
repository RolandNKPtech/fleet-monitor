"""Resolve apex domains to GA4 property_id and GSC site_url.

Auto-derives from the analytics lake's meta tables (`meta/properties.parquet`
and `meta/sites.parquet`). A hand-edited YAML overrides file forces specific
mappings when auto-match is wrong or missing.

No I/O outside `build_mapping`. The returned dict is JSON-friendly.
"""
from __future__ import annotations
from pathlib import Path

import yaml
import pyarrow.parquet as pq


def _strip_www(apex: str) -> str:
    return apex[4:] if apex.startswith("www.") else apex


def _load_overrides(overrides_path: Path) -> dict[str, dict]:
    if not overrides_path.exists():
        return {}
    raw = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
    out = {}
    for entry in raw.get("overrides") or []:
        apex = entry.get("apex")
        if not apex:
            continue
        out[apex] = {
            "ga4_property_id": entry.get("ga4_property_id"),
            "gsc_site_url": entry.get("gsc_site_url"),
        }
    return out


def _read_table(parquet_path: Path) -> list[dict]:
    if not parquet_path.exists():
        return []
    return pq.read_table(parquet_path).to_pylist()


def build_mapping(apexes: list[str], lake_path: Path,
                  overrides_path: Path) -> dict[str, dict]:
    """Return {apex: {ga4_property_id, ga4_source, gsc_site_url, gsc_source}}.

    `ga4_source` / `gsc_source` is one of "auto", "override", or None (no coverage).
    """
    overrides = _load_overrides(overrides_path)
    props = _read_table(Path(lake_path) / "meta" / "properties.parquet")
    sites = _read_table(Path(lake_path) / "meta" / "sites.parquet")
    sites_by_host = {row["host"]: row["site_url"] for row in sites
                     if row.get("host")}

    out: dict[str, dict] = {}
    for apex in apexes:
        key_apex = _strip_www(apex)
        override = overrides.get(apex) or overrides.get(key_apex) or {}

        # GA4: auto-match = property_name contains the apex (case-insensitive).
        ga4_id = override.get("ga4_property_id")
        ga4_source = "override" if ga4_id else None
        if not ga4_id:
            matches = [p["property_id"] for p in props
                       if p.get("property_name")
                       and key_apex.lower() in p["property_name"].lower()]
            if matches:
                # When several properties match (e.g. legacy + current), pick
                # the smallest property_id string as a stable, deterministic
                # tiebreak.
                # TODO(plan-2): swap for highest-sessions tiebreak per spec §5.
                ga4_id = sorted(matches)[0]
                ga4_source = "auto"

        # GSC: direct host-equality on meta.sites.host.
        gsc_url = override.get("gsc_site_url")
        gsc_source = "override" if gsc_url else None
        if not gsc_url:
            gsc_url = sites_by_host.get(key_apex)
            if gsc_url:
                gsc_source = "auto"

        out[apex] = {
            "ga4_property_id": ga4_id,
            "ga4_source": ga4_source,
            "gsc_site_url": gsc_url,
            "gsc_source": gsc_source,
        }
    return out
