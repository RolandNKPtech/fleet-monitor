"""Fleet cost projection from per-zone CF plan info.

Honest framing: this is COST PROJECTION from CF's published plan prices
(zone.plan.price + zone.plan.frequency) × what we see in the snapshot. It
is NOT a mirror of actual invoices — CF doesn't expose those at the API
layer below Enterprise tier. The projection covers the recurring
subscription side only; usage-based costs (Workers requests, R2 storage,
Argo bandwidth) are NOT included here and need separate fetch paths.

The aim is the operator's monthly-budget conversation:
  "What's our CF subscription bill running per month? Per plan tier?"
"""
from __future__ import annotations


_MONTHLY_FROM_FREQ = {
    "monthly": 1.0,
    "quarterly": 1.0 / 3,
    "yearly": 1.0 / 12,
    "annual": 1.0 / 12,
}


def _monthly(price, frequency) -> float:
    """Convert a plan's (price, frequency) to monthly run-rate USD.

    Unknown frequencies treat the price as already monthly — safer to
    over-project than to silently zero out an unfamiliar billing cadence.
    """
    if price is None:
        return 0.0
    try:
        p = float(price)
    except (TypeError, ValueError):
        return 0.0
    factor = _MONTHLY_FROM_FREQ.get((frequency or "monthly").lower(), 1.0)
    return p * factor


def summarize(snapshot: dict) -> dict:
    """Reduce a snapshot to a fleet-wide CF subscription cost summary.

    Returns:
      {
        total_monthly_usd: 1234.50,
        zone_count_with_plan: 268,
        zone_count_without_plan: 0,
        by_plan: [
          {name: "Free Website", count: 240, monthly_usd: 0, price_each: 0},
          {name: "Pro Website",  count:  25, monthly_usd: 500, price_each: 20},
          {name: "Business Website", count: 3, monthly_usd: 600, price_each: 200},
        ],
        currency: "USD",
      }

    Sites with no cf.plan block (cf-only sites without zone-list data, or
    zones whose plan field is missing from CF) are counted in
    `zone_count_without_plan` so the operator sees the gap rather than a
    fabricated zero.
    """
    by_plan: dict[str, dict] = {}
    total_monthly = 0.0
    with_plan = 0
    without_plan = 0
    currency = "USD"

    for site in snapshot.get("sites", []):
        cf = site.get("cf")
        if cf is None:
            continue   # wpe-only — no CF zone exists, not a billing target
        plan = (cf or {}).get("plan")
        if not plan or not plan.get("name"):
            without_plan += 1   # CF zone present but plan info missing
            continue
        with_plan += 1
        name = plan["name"]
        monthly = _monthly(plan.get("price"), plan.get("frequency"))
        total_monthly += monthly
        cur = plan.get("currency") or "USD"
        if cur != "USD":
            # Honest: we don't currency-convert. Mark mixed-currency case.
            currency = "mixed"
        bucket = by_plan.setdefault(name, {
            "name": name, "count": 0, "monthly_usd": 0.0,
            "price_each": _monthly(plan.get("price"), plan.get("frequency")),
        })
        bucket["count"] += 1
        bucket["monthly_usd"] += monthly

    # Sort by monthly_usd descending — biggest line items first.
    plans = sorted(by_plan.values(),
                   key=lambda p: (-p["monthly_usd"], p["name"]))
    for p in plans:
        p["monthly_usd"] = round(p["monthly_usd"], 2)
        p["price_each"] = round(p["price_each"], 2)

    return {
        "total_monthly_usd": round(total_monthly, 2),
        "zone_count_with_plan": with_plan,
        "zone_count_without_plan": without_plan,
        "by_plan": plans,
        "currency": currency,
    }
