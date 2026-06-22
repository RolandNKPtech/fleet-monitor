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


def load_plans() -> dict[str, AccountPlan]:
    """Read wpe-plans.yml and return {lookup_name: AccountPlan}.

    Each plan is registered under its YAML key AND every name in
    `real_account_names:` — so a downstream lookup by the snapshot's real
    WPE account name (e.g. `nkpmedical1`) finds the same AccountPlan that
    the YAML keyed under a sanitized alias (e.g. `acctA`). Multiple keys
    point at the *same* AccountPlan instance; dedupe by `display_label`
    when iterating for display.

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
    return out


def primary_lookup_name(plan: AccountPlan) -> str:
    """The real WPE account name to use for daily_rows / cycle math.

    When `real_account_names` is set, use the first entry (the snapshot
    reports one name per install). Otherwise the display label IS the
    real name (back-compat with private/un-aliased configs).
    """
    return plan.real_account_names[0] if plan.real_account_names else plan.display_label
