"""Per-site rolling baselines — trailing median, robust to the spikes we hunt."""
from __future__ import annotations
import statistics

BASELINE_WINDOW = 14   # snapshots
BASELINE_MIN = 7       # need at least this many real values


def baseline(values: list) -> float | None:
    """Trailing median of up to BASELINE_WINDOW values. None if < BASELINE_MIN snapshots present.

    BASELINE_MIN is checked against total snapshots (including None entries) so that
    a site with enough history but occasional missing data still gets a baseline.
    None entries are excluded from the median computation itself.
    """
    window = values[-BASELINE_WINDOW:]
    if len(window) < BASELINE_MIN:
        return None
    vals = [v for v in window if v is not None]
    if not vals:
        return None
    return statistics.median(vals)


def metric_history(history: list[dict], path: tuple) -> list:
    """Extract one metric across history entries. path e.g. ('wpe','bandwidth_gb_30d')."""
    out = []
    for entry in history:
        v = entry
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        out.append(v)
    return out
