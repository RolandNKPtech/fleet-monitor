"""Tests for the secondary storage progress bar on Plan Utilization cards.

The storage bar reads from two sources:
  - usage: sum of snapshot's wpe.storage_gb across installs per account
  - cap:   AccountPlan.storage_gb_limit (auto-fetched from /accounts/{id}/limits)

Independent of cycle_start_day (storage is a current snapshot, not a cycle
metric), so the bar must show on BOTH the configured-with-cycle branch
AND the partial-config branch.
"""
from datetime import date
from projects.fleet_monitoring.plan_config import AccountPlan
from projects.fleet_monitoring.render import _storage_row, _plan_utilization_panel


def test_storage_row_returns_empty_when_either_value_missing():
    """No cap or no usage -> silent render (no row), never a guess."""
    assert _storage_row(used_gb=100, cap_gb=None) == ""
    assert _storage_row(used_gb=None, cap_gb=300) == ""
    assert _storage_row(used_gb=None, cap_gb=None) == ""
    # Defensive: zero or negative cap can't anchor a percent.
    assert _storage_row(used_gb=100, cap_gb=0) == ""


def test_storage_row_severity_thresholds_match_bandwidth_bar():
    """Storage bar must use the same 80/95 thresholds as the bandwidth bar
    so operators reading both see one mental model. Anything above 95% is
    critical (red), 80-95 warning (yellow), below 80 good (green/empty)."""
    # 50% -> good, no severity class.
    html = _storage_row(used_gb=150, cap_gb=300)
    assert "sev-critical" not in html and "sev-warning" not in html

    # 85% -> warning.
    html = _storage_row(used_gb=255, cap_gb=300)
    assert "sev-warning" in html
    assert "sev-critical" not in html

    # 97% -> critical.
    html = _storage_row(used_gb=291, cap_gb=300)
    assert "sev-critical" in html


def test_storage_row_handles_over_plan_correctly():
    """A site at 106% storage shouldn't crash or render a bar wider than
    100%. The percentage label honestly shows 106%; the visual bar caps."""
    html = _storage_row(used_gb=370, cap_gb=350)
    # Honest pct in the label.
    assert "106%" in html
    assert "of 350 GB" in html
    # Bar width is clamped — no width:106% leaking through to the CSS.
    assert "width:100.0%" in html
    assert "sev-critical" in html


def test_storage_row_shows_headroom_when_under_cap():
    """Headroom helps the operator gauge how much runway is left without
    doing the arithmetic in their head."""
    html = _storage_row(used_gb=100, cap_gb=300)
    assert "headroom 200 GB" in html


def test_plan_panel_aggregates_storage_per_account_through_alias():
    """Sum the wpe.storage_gb across installs into account-level totals,
    routed through the same alias resolution as bandwidth so a card keyed
    on a sanitized label still joins to the real-name install rows."""
    plan = AccountPlan(bandwidth_gb_limit=1000,
                       storage_gb_limit=300,
                       display_label="acctA",
                       real_account_names=["nkpmedical1"])
    plans = {"acctA": plan, "nkpmedical1": plan}
    snap = {"sites": [
        {"key": "site1.com", "wpe": {"account_name": "nkpmedical1",
                                      "bandwidth_gb_30d": 100,
                                      "storage_gb": 50}},
        {"key": "site2.com", "wpe": {"account_name": "nkpmedical1",
                                      "bandwidth_gb_30d": 200,
                                      "storage_gb": 75}},
    ]}
    html = _plan_utilization_panel(today=date(2026, 6, 23),
                                   plans=plans, daily_rows=[], snapshot=snap)
    # 50 + 75 = 125 of 300 -> 42%. The label spans an inline <strong>,
    # so check the two pieces separately rather than the continuous text.
    assert "<strong>125</strong> of 300 GB" in html
    assert "<strong>42%</strong>" in html
    # Storage row actually rendered (vs silently skipped because of missing
    # cycle_start_day — storage doesn't depend on the cycle anchor).
    assert "plan-storage-row" in html


def test_plan_panel_silent_when_storage_cap_unset():
    """When YAML + auto-fetch both leave storage_gb_limit None, the bar
    must not render — same silent-when-unknown policy as bandwidth."""
    plan = AccountPlan(bandwidth_gb_limit=1000,
                       storage_gb_limit=None,  # not set
                       display_label="x",
                       real_account_names=["x"])
    snap = {"sites": [{"key": "s.com", "wpe": {"account_name": "x",
                                                "bandwidth_gb_30d": 50,
                                                "storage_gb": 30}}]}
    html = _plan_utilization_panel(today=date(2026, 6, 23),
                                   plans={"x": plan}, daily_rows=[], snapshot=snap)
    assert "plan-storage-row" not in html
