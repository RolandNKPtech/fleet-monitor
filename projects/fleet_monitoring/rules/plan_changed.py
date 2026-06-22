"""Rule: CF zone plan tier changed since the previous snapshot.

Catches the silent billing event of a teammate upgrading a zone Free->Pro
(or downgrading Pro->Free, which loses features). Severity is warning by
default — plan changes are usually intentional and need an operator to
review whether the change was sanctioned, not page someone at 2am.

Compares the current snapshot's `cf.plan.name` against the most recent
prior snapshot in history. No alert on the first observation (history
empty) — a baseline is needed before "change" has meaning.
"""
from __future__ import annotations
from ..models import Alert, SEVERITY_WARNING

RULE_ID = "plan_changed"


def _plan_name(entry: dict) -> str | None:
    return ((entry.get("cf") or {}).get("plan") or {}).get("name")


def _plan_price(entry: dict) -> float | None:
    p = ((entry.get("cf") or {}).get("plan") or {}).get("price")
    try:
        return float(p) if p is not None else None
    except (TypeError, ValueError):
        return None


def evaluate(site: dict, history: list[dict]) -> list[Alert]:
    current = _plan_name(site)
    if current is None:
        return []   # zone has no plan info — nothing to compare against
    # Walk history newest-first to find the most recent prior with a plan.
    prior = None
    for entry in reversed(history or []):
        n = _plan_name(entry)
        if n:
            prior = entry
            break
    if prior is None or _plan_name(prior) == current:
        return []
    old_name = _plan_name(prior)
    new_price = _plan_price(site) or 0
    old_price = _plan_price(prior) or 0
    direction = ("upgrade" if new_price > old_price
                 else "downgrade" if new_price < old_price
                 else "rename")
    return [Alert(
        site_key=site["key"], rule=RULE_ID, severity=SEVERITY_WARNING,
        summary=(f"CF plan {direction}: {old_name} -> {current} "
                 f"(${old_price:g}/mo -> ${new_price:g}/mo) — "
                 f"verify the change was sanctioned"),
        detail={
            "old_plan": old_name, "new_plan": current,
            "old_price": old_price, "new_price": new_price,
            "direction": direction,
        },
    )]
