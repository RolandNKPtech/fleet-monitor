"""Loader for config/wpe-plans.yml — per-WPE-account plan caps.

Returned objects have explicit None for unconfigured fields, never
defaulting to a guessed value. Downstream code consults
`account_is_configured(plan)` before computing any "% of plan" metric.
"""
from __future__ import annotations
from dataclasses import dataclass
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


def account_is_configured(plan: AccountPlan) -> bool:
    """True when both cycle anchor AND a bandwidth limit are set.

    The plan-utilization analyzer skips accounts that aren't configured —
    it cannot compute % of plan or projection without both fields.
    """
    return (plan.cycle_start_day is not None
            and plan.bandwidth_gb_limit is not None)


def load_plans() -> dict[str, AccountPlan]:
    """Read wpe-plans.yml and return {account_name: AccountPlan}.

    Returns {} if the file is absent or empty. Unknown fields are ignored.
    """
    if not PLAN_FILE.exists():
        return {}
    data = yaml.safe_load(PLAN_FILE.read_text(encoding="utf-8")) or {}
    accounts = data.get("accounts") or {}
    out: dict[str, AccountPlan] = {}
    for name, fields in accounts.items():
        fields = fields or {}
        out[name] = AccountPlan(
            cycle_start_day=fields.get("cycle_start_day"),
            bandwidth_gb_limit=fields.get("bandwidth_gb_limit"),
            visits_limit=fields.get("visits_limit"),
            overage_per_gb_usd=fields.get("overage_per_gb_usd"),
        )
    return out
