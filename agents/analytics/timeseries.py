"""
Time Series Intelligence Engine — detect trends, seasonality, and anomalies
in time-series data.

Only activates when the schema intelligence layer finds a time column.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .schema_intel import detect_monetary_columns, detect_time_column


def _detect_anomalies_zscore(series: pd.Series, threshold: float = 2.5) -> pd.Series:
    if len(series) < 5 or series.std() == 0:
        return pd.Series([False] * len(series), index=series.index)
    z = (series - series.mean()) / series.std()
    return z.abs() > threshold


def _detect_anomalies_iqr(series: pd.Series, multiplier: float = 1.5) -> pd.Series:
    if len(series) < 5:
        return pd.Series([False] * len(series), index=series.index)
    q1, q3 = series.quantile(0.25), series.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return pd.Series([False] * len(series), index=series.index)
    lower, upper = q1 - multiplier * iqr, q3 + multiplier * iqr
    return (series < lower) | (series > upper)


def _trend_slope(series: pd.Series) -> float:
    x = np.arange(len(series))
    y = series.values
    if len(x) < 2 or np.isclose(np.var(x), 0):
        return 0.0
    slope = np.polyfit(x, y, 1)[0]
    return round(float(slope), 4)


def _detect_structural_break(series: pd.Series, window: int = 3) -> list[dict]:
    breaks = []
    if len(series) < window * 2 + 1:
        return breaks
    for i in range(window, len(series) - window):
        before = series.iloc[i - window:i]
        after = series.iloc[i + 1:i + window + 1]
        before_mean, after_mean = before.mean(), after.mean()
        if before_mean == 0:
            continue
        change_pct = (after_mean - before_mean) / abs(before_mean) * 100
        if abs(change_pct) > 20:
            breaks.append({
                "index": int(i),
                "date": str(series.index[i]) if hasattr(series.index, "dtype") else str(i),
                "before_avg": round(float(before_mean), 2),
                "after_avg": round(float(after_mean), 2),
                "change_pct": round(float(change_pct), 2),
            })
    return breaks


def compute_timeseries(df: pd.DataFrame) -> dict:
    time_col = detect_time_column(df)
    if time_col is None:
        return {"available": False, "reason": "no time column detected"}

    monetary_cols = detect_monetary_columns(df)
    result = {
        "available": True,
        "time_column": time_col,
        "aggregations": {},
    }

    try:
        df_ts = df.copy()
        df_ts[time_col] = pd.to_datetime(df_ts[time_col], errors="coerce")
        df_ts = df_ts.dropna(subset=[time_col]).sort_values(time_col)
        if df_ts.empty:
            return {**result, "reason": "time column empty after parse"}

        ts_col = monetary_cols[0] if monetary_cols else None
        if ts_col is None:
            ts_col = df_ts.select_dtypes(include=[np.number]).columns[0]
        ts_col = str(ts_col)

        # ── Daily aggregation ──────────────────────────────────────────
        daily = df_ts.set_index(time_col).resample("D")[ts_col].sum().dropna()
        if not daily.empty:
            result["aggregations"]["daily"] = {
                "count": int(len(daily)),
                "min_date": str(daily.index.min().date()),
                "max_date": str(daily.index.max().date()),
                "mean": round(float(daily.mean()), 2),
                "std": round(float(daily.std()), 2),
                "min": round(float(daily.min()), 2),
                "max": round(float(daily.max()), 2),
            }

        # ── Weekly aggregation ─────────────────────────────────────────
        weekly = df_ts.set_index(time_col).resample("W")[ts_col].sum().dropna()
        if not weekly.empty:
            result["aggregations"]["weekly"] = {
                "count": int(len(weekly)),
                "mean": round(float(weekly.mean()), 2),
                "std": round(float(weekly.std()), 2),
                "min": round(float(weekly.min()), 2),
                "max": round(float(weekly.max()), 2),
            }

        # ── Rolling means ──────────────────────────────────────────────
        if not daily.empty and len(daily) >= 7:
            result["rolling_mean_7d"] = {
                str(d.date()): round(float(v), 2)
                for d, v in daily.rolling(7).mean().dropna().tail(30).items()
            }
        if not daily.empty and len(daily) >= 14:
            result["rolling_mean_14d"] = {
                str(d.date()): round(float(v), 2)
                for d, v in daily.rolling(14).mean().dropna().tail(30).items()
            }
        if not daily.empty and len(daily) >= 30:
            result["rolling_mean_30d"] = {
                str(d.date()): round(float(v), 2)
                for d, v in daily.rolling(30).mean().dropna().tail(30).items()
            }

        # ── Trend slope ────────────────────────────────────────────────
        agg_series = daily if not daily.empty else weekly
        if not agg_series.empty:
            result["trend_slope"] = _trend_slope(agg_series)
            result["trend_direction"] = (
                "up" if result["trend_slope"] > 0 else "down" if result["trend_slope"] < 0 else "flat"
            )

        # ── Anomaly detection ──────────────────────────────────────────
        if not daily.empty and len(daily) >= 7:
            anomalies_z = _detect_anomalies_zscore(daily)
            anomalies_iqr = _detect_anomalies_iqr(daily)
            anomaly_dates_z = anomalies_z[anomalies_z].index.tolist()
            anomaly_dates_iqr = anomalies_iqr[anomalies_iqr].index.tolist()
            result["anomalies"] = {
                "z_score_count": int(anomalies_z.sum()),
                "iqr_count": int(anomalies_iqr.sum()),
                "z_score_dates": [str(d.date()) for d in anomaly_dates_z[:20]],
                "iqr_dates": [str(d.date()) for d in anomaly_dates_iqr[:20]],
            }

            # spikes / drops
            spike_dates, drop_dates = [], []
            for d in anomaly_dates_z:
                val = daily.loc[d]
                mean_val = daily.mean()
                if val > mean_val * 1.5:
                    spike_dates.append(str(d.date()))
                elif val < mean_val * 0.5:
                    drop_dates.append(str(d.date()))
            result["spikes"] = spike_dates[:10]
            result["drops"] = drop_dates[:10]
        else:
            result["anomalies"] = {"z_score_count": 0, "iqr_count": 0}

        # ── Structural breaks ──────────────────────────────────────────
        if not daily.empty and len(daily) >= 10:
            breaks = _detect_structural_break(daily, window=min(3, len(daily) // 3))
            result["structural_breaks"] = breaks[:5]
        else:
            result["structural_breaks"] = []

    except Exception as exc:
        result["error"] = str(exc)

    return result
