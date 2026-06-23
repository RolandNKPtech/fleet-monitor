"""Loader for config/wpe-plans.yml — per-WPE-account plan caps.

Returned objects have explicit None for unconfigured fields, never
defaulting to a guessed value. Downstream code consults
`account_is_configured(plan)` before computing any "% of plan" metric.

The YAML key is the *display label* shown on the dashboard. By default
that label IS the real WPE account name (back-compat). When the public
repo needs a sanitized display label, list the real WPE-API account
names under `real_account_names:` so the live snapshot data still joins.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import PROJECT_DIR

PLAN_FILE = PROJECT_DIR / "config" / "wpe-plans.yml"


@dataclass
class AccountPlan:
    cycle_start_day: int | None = None
    bandwidth_gb_limit: float | None = None
    visits_limit: int | None = None
    overage_per_gb_usd: float | None = None
    display_label: str = ""
    real_account_names: list[str] = field(default_factory=list)


def account_is_configured(plan: AccountPlan) -> bool:
    """True when both cycle anchor AND a bandwidth limit are set.

    The plan-utilization analyzer skips accounts that aren't configured —
    it cannot compute % of plan or projection without both fields.
    """
    return (plan.cycle_start_day is not None
            and plan.bandwidth_gb_limit is not None)


def load_plans(*, fetch_live_limits: bool = False) -> dict[str, AccountPlan]:
    """Read wpe-plans.yml and return {lookup_name: AccountPlan}.

    Each plan is registered under its YAML key AND every name in
    `real_account_names:` — so a downstream lookup by the snapshot's real
    WPE account name (e.g. `nkpmedical1`) finds the same AccountPlan that
    the YAML keyed under a sanitized alias (e.g. `acctA`). Multiple keys
    point at the *same* AccountPlan instance; dedupe by `display_label`
    when iterating for display.

    `fetch_live_limits` (opt-in) triggers a WPE API call per account to
    fill in any YAML field left null with the live `/accounts/{id}/limits`
    response (bandwidth + visitors). YAML always wins — the live fetch
    only fills nulls. Tests leave this False to stay deterministic; the
    real pipeline (analyze stage) passes True.

    Returns {} if the file is absent or empty. Unknown fields are ignored.
    """
    if not PLAN_FILE.exists():
        return {}
    data = yaml.safe_load(PLAN_FILE.read_text(encoding="utf-8")) or {}
    accounts = data.get("accounts") or {}
    out: dict[str, AccountPlan] = {}
    for name, fields_dict in accounts.items():
        fields_dict = fields_dict or {}
        real_names = list(fields_dict.get("real_account_names") or [])
        plan = AccountPlan(
            cycle_start_day=fields_dict.get("cycle_start_day"),
            bandwidth_gb_limit=fields_dict.get("bandwidth_gb_limit"),
            visits_limit=fields_dict.get("visits_limit"),
            overage_per_gb_usd=fields_dict.get("overage_per_gb_usd"),
            display_label=name,
            real_account_names=real_names or [name],
        )
        out[name] = plan
        for alias in real_names:
            out[alias] = plan
    if fetch_live_limits and _wpe_credentials_present():
        _apply_live_limits(out)
    return out


def _wpe_credentials_present() -> bool:
    """True when both WPE API env vars are non-empty. No-op safety guard
    so dev/test environments without WPE creds never trigger the network
    fetch path."""
    return bool(os.environ.get("WPE_API_USER")
                and os.environ.get("WPE_API_PASSWORD"))


def _apply_live_limits(plans: dict[str, AccountPlan]) -> None:
    """For each plan with null bandwidth/visits caps, fill from
    `/accounts/{id}/limits`. YAML values always win. Failure-isolated —
    a WPE API outage during analyze must never crash the pipeline; it
    just leaves the YAML defaults in place (operator sees "plan limit
    not set" until the next run when the API recovers).

    Imports wpe_api lazily so unit tests don't have to deal with the
    boto3-style transitive cost of the live module."""
    try:
        from . import wpe_api
        accounts = wpe_api.list_accounts()
    except Exception:                              # pragma: no cover
        return
    if not accounts:
        return
    name_to_id = {a.get("name"): a.get("id") for a in accounts if a.get("id")}

    # Dedupe — same plan may appear under multiple keys (display_label
    # plus every alias in real_account_names).
    seen: set[int] = set()
    for plan in plans.values():
        if id(plan) in seen:
            continue
        seen.add(id(plan))
        # Skip the network call when both fields the API can fill are
        # already populated from YAML.
        if (plan.bandwidth_gb_limit is not None
                and plan.visits_limit is not None):
            continue
        for real_name in plan.real_account_names:
            acct_id = name_to_id.get(real_name)
            if not acct_id:
                continue
            try:
                limits = wpe_api.get_account_limits(acct_id)
            except Exception:                       # pragma: no cover
                limits = None
            if not limits:
                continue
            if plan.bandwidth_gb_limit is None and limits.get("bandwidth"):
                plan.bandwidth_gb_limit = float(limits["bandwidth"])
            if plan.visits_limit is None and limits.get("visitors"):
                plan.visits_limit = int(limits["visitors"])
            break  # first matching alias is enough


def primary_lookup_name(plan: AccountPlan) -> str:
    """The real WPE account name to use for daily_rows / cycle math.

    When `real_account_names` is set, use the first entry (the snapshot
    reports one name per install). Otherwise the display label IS the
    real name (back-compat with private/un-aliased configs).
    """
    return plan.real_account_names[0] if plan.real_account_names else plan.display_label
