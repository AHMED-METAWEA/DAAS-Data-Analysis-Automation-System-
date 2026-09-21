"""
Forecast evaluation metrics — MAE, RMSE, MAPE, and MASE (deterministic).
"""

from __future__ import annotations

import numpy as np


def naive_scale(train_y: np.ndarray, m: int = 1) -> float | None:
    """MASE's scale factor: mean absolute *m*-step naive difference over the
    training series (Hyndman & Koehler, 2006). Unlike MAPE, this never blows
    up when actuals are near zero or negative — it's the standard fix for
    MAPE's best-known pathology, used as the headline metric in the M4/M5
    forecasting competitions. ``None`` when there isn't enough history to
    compute a stable scale (all models then fall back to MAPE-only ranking).
    """
    y = np.asarray(train_y, dtype=float)
    if len(y) <= m:
        return None
    diffs = np.abs(y[m:] - y[:-m])
    scale = float(np.mean(diffs))
    return scale if scale > 1e-9 else None


def squared_naive_scale(train_y: np.ndarray, m: int = 1) -> float | None:
    """RMSSE's scale factor: root-mean-*squared* m-step naive difference.

    The squared counterpart of :func:`naive_scale`, and the denominator of the
    metric the M5 competition ranked on.
    """
    y = np.asarray(train_y, dtype=float)
    if len(y) <= m:
        return None
    diffs = y[m:] - y[:-m]
    scale = float(np.sqrt(np.mean(diffs ** 2)))
    return scale if scale > 1e-9 else None


def evaluate_forecast(
    actual: np.ndarray,
    predicted: np.ndarray,
    scale: float | None = None,
    sq_scale: float | None = None,
) -> dict[str, float | None]:
    """Compute error metrics between aligned actual and predicted arrays.

    ``scale`` / ``sq_scale`` — the MASE and RMSSE denominators — are per-series
    constants computed once from the training history and shared across every
    candidate model, so the scaled errors stay directly comparable.

    Both scaled metrics are reported because they answer different questions,
    and picking the wrong one biases every published number:

    * **MASE** is minimised by the conditional *median*.
    * **RMSSE** is minimised by the conditional *mean*.

    Business series are right-skewed, so those differ — and the headline figure
    this system publishes is a horizon **total**, which is a sum of means. Rank
    an additive metric on MASE and you systematically select the model that
    under-predicts the total; that is exactly what produced 22-36% shortfalls
    in the horizon totals here while the per-period error looked respectable.
    """
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    n = min(len(actual), len(predicted))
    if n == 0:
        return {"mae": None, "rmse": None, "mape": None, "mase": None, "rmsse": None}

    actual, predicted = actual[:n], predicted[:n]
    err = actual - predicted
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))

    mask = np.abs(actual) > 1e-9
    if mask.any():
        mape = float(np.mean(np.abs(err[mask] / actual[mask])) * 100)
    else:
        mape = None

    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 2) if mape is not None else None,
        "mase": round(mae / scale, 4) if scale else None,
        "rmsse": round(rmse / sq_scale, 4) if sq_scale else None,
    }


def evaluate_in_sample(
    y: np.ndarray,
    fitted: np.ndarray | None,
    holdout: int = 0,
) -> dict[str, float | None]:
    """Evaluate in-sample fit; optional tail holdout for pseudo-out-of-sample MAE."""
    y = np.asarray(y, dtype=float)
    if fitted is None or len(fitted) == 0:
        return {"mae": None, "rmse": None, "mape": None}

    fitted = np.asarray(fitted, dtype=float)
    n = min(len(y), len(fitted))
    y, fitted = y[-n:], fitted[-n:]

    if holdout > 0 and n > holdout + 5:
        return evaluate_forecast(y[-holdout:], fitted[-holdout:])

    return evaluate_forecast(y, fitted)
