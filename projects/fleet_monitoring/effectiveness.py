"""Intervention effectiveness — target metric averaged before vs after a fix."""
from __future__ import annotations
import sqlite3
from datetime import date, timedelta

HORIZONS = (7, 30, 90)
BEFORE_DAYS = 14
MIN_BEFORE_DAYS = 3
SIGNIFICANCE_PCT = 10.0

LOWER_IS_BETTER = {"bandwidth", "mb_per_visit", "storage"}
HIGHER_IS_BETTER: set[str] = set()          # none in v1
VALID_TARGETS = LOWER_IS_BETTER

# target_metric -> metrics-table column
METRIC_COLUMN = {
    "bandwidth": "bandwidth_gb",
    "mb_per_visit": "mb_per_visit",
    "storage": "storage_gb",
}


def _as_date(v) -> date:
    return v if isinstance(v, date) else date.fromisoformat(v)


def _window_values(series: dict, start: date, end: date) -> list[float]:
    """Metric values present on each day in [start, end] inclusive."""
    out = []
    d = start
    while d <= end:
        v = series.get(d.isoformat())
        if v is not None:
            out.append(float(v))
        d += timedelta(days=1)
    return out


def _mean(values: list[float]):
    return sum(values) / len(values) if values else None


def compute_one(metric_series: dict, applied_date, target_metric: str,
                today) -> list[dict]:
    """Effectiveness for one intervention. One result dict per horizon.

    `metric_series` is {date_iso: value} for one site/metric.
    Each result: {horizon_days, before_avg, after_avg, delta_pct, verdict}.
    """
    applied = _as_date(applied_date)
    today_d = _as_date(today)
    before_vals = _window_values(
        metric_series, applied - timedelta(days=BEFORE_DAYS),
        applied - timedelta(days=1))
    before_avg = _mean(before_vals)
    lower_better = target_metric in LOWER_IS_BETTER

    results = []
    for h in HORIZONS:
        if before_avg is None or len(before_vals) < MIN_BEFORE_DAYS:
            results.append({"horizon_days": h, "before_avg": before_avg,
                            "after_avg": None, "delta_pct": None,
                            "verdict": "baseline_unavailable"})
            continue
        after_end = applied + timedelta(days=h)
        after_vals = _window_values(
            metric_series, applied + timedelta(days=1), after_end)
        after_avg = _mean(after_vals)
        if today_d < after_end or (len(after_vals) / h) < 0.5:
            results.append({"horizon_days": h, "before_avg": before_avg,
                            "after_avg": after_avg, "delta_pct": None,
                            "verdict": "too_early"})
            continue
        delta_pct = ((after_avg - before_avg) / before_avg * 100.0
                     if before_avg else 0.0)
        good = (delta_pct <= -SIGNIFICANCE_PCT if lower_better
                else delta_pct >= SIGNIFICANCE_PCT)
        bad = (delta_pct >= SIGNIFICANCE_PCT if lower_better
               else delta_pct <= -SIGNIFICANCE_PCT)
        verdict = "worked" if good else ("regressed" if bad else "no_effect")
        results.append({"horizon_days": h, "before_avg": before_avg,
                        "after_avg": after_avg,
                        "delta_pct": round(delta_pct, 1), "verdict": verdict})
    return results


def compute(db_path, today) -> None:
    """Fill the effectiveness table from the interventions + metrics tables.

    Replaces the table each run. Interventions whose target_metric is not in
    VALID_TARGETS get no rows (the dashboard shows 'metric not supported').
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("DELETE FROM effectiveness")
        interventions = conn.execute(
            "SELECT id, site_key, applied_date, target_metric "
            "FROM interventions").fetchall()
        out_rows = []
        for iv in interventions:
            tm = iv["target_metric"]
            column = METRIC_COLUMN.get(tm)
            if tm not in VALID_TARGETS or column is None:
                continue
            series = {
                r["date"]: r[column]
                for r in conn.execute(
                    f"SELECT date, {column} FROM metrics WHERE site_key = ? "
                    f"AND {column} IS NOT NULL", (iv["site_key"],)).fetchall()
            }
            for res in compute_one(series, iv["applied_date"], tm, today):
                out_rows.append({
                    "intervention_id": iv["id"],
                    "horizon_days": res["horizon_days"],
                    "before_avg": res["before_avg"],
                    "after_avg": res["after_avg"],
                    "delta_pct": res["delta_pct"],
                    "verdict": res["verdict"]})
        conn.executemany(
            "INSERT INTO effectiveness "
            "(intervention_id, horizon_days, before_avg, after_avg, delta_pct, verdict) "
            "VALUES (:intervention_id, :horizon_days, :before_avg, :after_avg, "
            ":delta_pct, :verdict)", out_rows)
        conn.commit()
    finally:
        conn.close()
