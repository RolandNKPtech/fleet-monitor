"""Intervention log — CF-drift-drafted, human-confirmed site-fix records.

`config/interventions.yml` is the human-authoritative list. The detector only
ever APPENDS new draft blocks (never rewrites the file), so human edits and
comments are never clobbered.
"""
from __future__ import annotations
import hashlib
import json

import yaml

from .models import INTERVENTIONS_FILE

_SEED = """\
# Fleet Monitoring — intervention log.
# Drafts are auto-appended from CF config drift with status: needs_review.
# To review a draft: set status to `confirmed` (correct target_metric /
# applied_date / type if the guess is wrong) or `dismissed` (not a fix).
# `fingerprint` is the dedup key — never edit it.
# You may also hand-add a confirmed entry for a fix that drift did not catch.
#
# target_metric: bandwidth | mb_per_visit | storage
interventions:
"""


def load_interventions(path=INTERVENTIONS_FILE) -> list[dict]:
    """Read interventions.yml -> the interventions list. [] when missing/empty.

    Raises ValueError on a file that is not a mapping with an `interventions`
    key — config is a system boundary, fail loud rather than silently empty.
    """
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return []
    if not isinstance(data, dict) or "interventions" not in data:
        raise ValueError(f"{path}: malformed — expected a top-level 'interventions:' key")
    return data.get("interventions") or []


def intervention_fingerprint(site: str, applied_date: str,
                             field: str, old, new) -> str:
    """Stable dedup key for a drift event: site:date:hash(field|old|new)."""
    h = hashlib.sha1(f"{field}|{old}|{new}".encode("utf-8")).hexdigest()[:8]
    return f"{site}:{applied_date}:{h}"


def _guess_type(kind: str) -> str:
    """Guess an intervention type from a digest_diff drift `kind`.

    Prefix-matched against the real digest.py vocabulary (waf_rule_*,
    cache_rule_*, ssl_*/tls_*, bot_toggle_*, dns_proxy_*).
    """
    kind = kind or ""
    if kind.startswith("waf_rule"):
        return "cf_waf_rule"
    if kind.startswith("cache_rule"):
        return "cf_cache_rule"
    if kind.startswith("ssl") or kind.startswith("tls"):
        return "cf_ssl"
    if kind.startswith("bot_toggle"):
        return "cf_bot"
    if kind.startswith("dns_proxy"):
        return "cf_proxy"
    return "cf_config_change"


def detect_drafts(snapshot: dict, existing: list[dict]) -> list[dict]:
    """New draft interventions from `us`-attributed config_drift alerts.

    Pure — returns the list of new draft dicts (status: needs_review),
    deduped against `existing` and within the snapshot, by fingerprint.
    """
    existing_fps = {e.get("fingerprint") for e in existing}
    applied_date = snapshot.get("date", "")
    seen: set[str] = set()
    drafts: list[dict] = []
    for a in snapshot.get("alerts", []):
        if a.get("rule") != "config_drift":
            continue
        detail = a.get("detail") or {}
        if detail.get("attribution") != "us":
            continue
        site = a.get("site_key", "")
        field = detail.get("field", "")
        old = detail.get("old", "")
        new = detail.get("new", "")
        kind = detail.get("kind", "")
        fp = intervention_fingerprint(site, applied_date, field, old, new)
        if fp in existing_fps or fp in seen:
            continue
        seen.add(fp)
        drafts.append({
            "site": site,
            "applied_date": applied_date,
            "type": _guess_type(kind),
            "target_metric": "bandwidth",
            "description": f"{field}: {old} -> {new} (kind: {kind})",
            "status": "needs_review",
            "fingerprint": fp,
        })
    return drafts


_DRAFT_KEYS = ("site", "applied_date", "type", "target_metric",
               "description", "status", "fingerprint")


def _yaml_scalar(v) -> str:
    """A double-quoted YAML scalar. json.dumps output is valid YAML and
    safely escapes colons, '->', quotes etc. in free-text fields."""
    return json.dumps("" if v is None else str(v))


def append_drafts(path, drafts: list[dict]) -> int:
    """Append draft blocks to interventions.yml. Pure append — never rewrites.

    Creates the seed file when missing/empty. Guards a missing trailing
    newline. Re-parses afterward and raises if the append corrupted the file.
    Returns the count appended (0 on empty input).
    """
    if not drafts:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        path.write_text(_SEED, encoding="utf-8")

    existing = path.read_text(encoding="utf-8")
    lead = "" if existing.endswith("\n") else "\n"

    blocks = []
    for d in drafts:
        lines = [f"  - {_DRAFT_KEYS[0]}: {_yaml_scalar(d.get(_DRAFT_KEYS[0]))}"]
        for key in _DRAFT_KEYS[1:]:
            lines.append(f"    {key}: {_yaml_scalar(d.get(key))}")
        blocks.append("\n".join(lines) + "\n")

    with path.open("a", encoding="utf-8") as f:
        f.write(lead + "".join(blocks))

    # Verify the append did not corrupt the file.
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("interventions"), list):
        raise ValueError(
            f"{path}: append corrupted the file — it no longer parses to an "
            f"'interventions' list")
    return len(drafts)
