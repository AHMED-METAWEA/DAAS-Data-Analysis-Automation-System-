"""
Forecasting model library.

Each model is a pure function with the signature:

    forecast(train_y, h, freq, train_dates=None) -> np.ndarray  (length h)

so that the back-tester ([backtest.py]) and the engine ([engines.py]) can treat
every model uniformly.  Heavy / optional dependencies (statsmodels, prophet) are
imported lazily inside their model so the module stays importable without them.

This replaces the previous "Prophet only" reality behind the `Auto` selector —
the engine now back-tests these candidates and picks the winner out-of-sample.
"""

from __future__ import annotations

import importlib.util
import warnings
from collections.abc import Callable

import numpy as np
import pandas as pd

def freq_to_pandas(freq: str) -> str:
    # Single source of truth lives in the preparation layer, so the grid the
    # history is built on and the grid the future dates are generated on can
    # never drift apart. (They previously did: history was resampled to "MS"
    # while future dates were generated at "ME", so monthly forecasts landed on
    # month-*end* stamps that matched no historical point at all.)
    from agents.forecasting.tools.preparation import freq_alias

    return freq_alias(freq)


def season_length(freq: str, n: int) -> int:
    """Dominant seasonal period for a frequency, capped so it fits the history."""
    # "none" is used internally to run a model on an already-deseasonalised
    # series (see the STL models) — it must not re-introduce a season.
    base = {"daily": 7, "weekly": 52, "monthly": 12, "none": 1}.get(freq, 7)
    if base >= n:
        base = max(1, n // 2)
    return base


# Every seasonal cycle a frequency can carry, longest last. Daily retail data
# has *two* real cycles — the within-week trading rhythm and the within-year
# season (this project's own data peaks hard in Q4) — and a model that only
# knows about one of them leaves the other in the residuals.
_SEASONAL_PERIODS = {
    "daily": (7.0, 365.25),
    "weekly": (52.18,),
    "monthly": (12.0,),
}


def seasonal_periods(freq: str, n: int) -> list[float]:
    """Seasonal cycles worth modelling given ``n`` observations.

    A cycle is only offered when the history covers it at least twice — one
    pass through a season is a coincidence, not a pattern.
    """
    return [p for p in _SEASONAL_PERIODS.get(freq, (7.0,)) if n >= 2 * p]


def _has(module: str | None) -> bool:
    return module is None or importlib.util.find_spec(module) is not None


# ── Baseline / classical models ─────────────────────────────────────────────


def naive(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Repeat the last observed value (random-walk baseline)."""
    last = float(train_y[-1]) if len(train_y) else 0.0
    return np.full(h, last, dtype=float)


def drift(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Last value plus the average per-step change over the history."""
    n = len(train_y)
    if n < 2:
        return naive(train_y, h, freq)
    slope = (float(train_y[-1]) - float(train_y[0])) / (n - 1)
    return float(train_y[-1]) + slope * np.arange(1, h + 1)


def _level(values: np.ndarray) -> float:
    """Level estimate for a flat forecast: the plain arithmetic mean.

    Deliberately *not* a median or a trimmed mean. Both are robust, but both
    estimate a central value below the mean on a right-skewed series — and
    business series are right-skewed. Since the published figure is a horizon
    **total**, and a total is the mean multiplied by the number of periods, any
    downward bias in the level becomes a proportional shortfall in every number
    the system reports. Measured here, switching a level estimator from median
    to mean moved the 30-day revenue total from ~33% low to within ~10%.

    Robustness is not given up, it is handled once and earlier: the training
    window has already been winsorised by the anomaly pass, so the outliers a
    trimmed mean would defend against have been capped before this sees them.
    Applying a second robust estimator on top only re-introduces the bias.
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    return float(np.mean(v)) if len(v) else 0.0


def _window_mean(train_y, h, freq, periods: int) -> np.ndarray:
    """Flat forecast at the mean of the last ``periods`` observations."""
    y = np.asarray(train_y, dtype=float)
    n = len(y)
    k = max(1, min(periods, n))
    return np.full(h, _level(y[-k:]), dtype=float)


def moving_average(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Flat forecast at the mean of the most recent season."""
    n = len(train_y)
    k = min(season_length(freq, n), n)
    if k < 1:
        return naive(train_y, h, freq)
    return np.full(h, float(np.mean(train_y[-k:])), dtype=float)


# On a noise-dominated series the best achievable forecast is a flat level, and
# *which* averaging window is best is a property of the data, not something to
# guess. Registering several windows as ordinary candidates lets the back-test
# answer it. Leaving only a one-season window in the pool — the previous
# behaviour — meant the model that actually wins on this project's daily data
# (roughly a four-week mean) was never on the board to be chosen.
def moving_average_2x(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Flat forecast at the trimmed mean of the last two seasons."""
    return _window_mean(train_y, h, freq, 2 * season_length(freq, len(train_y)))


def moving_average_4x(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Flat forecast at the trimmed mean of the last four seasons."""
    return _window_mean(train_y, h, freq, 4 * season_length(freq, len(train_y)))


def moving_average_long(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Flat forecast at the trimmed mean of the last third of the history."""
    n = len(train_y)
    return _window_mean(train_y, h, freq, max(season_length(freq, n), n // 3))


def seasonal_naive(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Repeat the last full season."""
    n = len(train_y)
    m = season_length(freq, n)
    if m < 2 or n < m:
        return naive(train_y, h, freq)
    last_season = np.asarray(train_y[-m:], dtype=float)
    reps = int(np.ceil(h / m))
    return np.tile(last_season, reps)[:h]


def linear_trend(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Ordinary-least-squares trend line extrapolated forward."""
    n = len(train_y)
    if n < 2:
        return naive(train_y, h, freq)
    x = np.arange(n)
    coef = np.polyfit(x, np.asarray(train_y, dtype=float), 1)
    return np.polyval(coef, np.arange(n, n + h))


# Damping factor for the trend models below. A straight line fitted to a short
# history and run forward assumes today's growth rate continues untouched for
# the whole horizon, which is why undamped trends were over-predicting this
# project's 3-month revenue totals by an average of ~49% (one origin by 101%).
# Damping shrinks each successive step's contribution by ``phi``, so the
# forecast still leans in the direction of growth but flattens out instead of
# running away. That damped trends beat undamped ones is among the most
# consistently replicated results in the forecasting literature (Gardner &
# McKenzie; every M-competition since).
_PHI = 0.90


def _damped_steps(h: int, phi: float = _PHI) -> np.ndarray:
    """Cumulative damped step multipliers: phi, phi+phi^2, phi+phi^2+phi^3, ..."""
    return np.cumsum(phi ** np.arange(1, h + 1))


def damped_drift(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Drift whose per-step slope decays geometrically over the horizon."""
    y = np.asarray(train_y, dtype=float)
    n = len(y)
    if n < 2:
        return naive(train_y, h, freq)
    slope = (float(y[-1]) - float(y[0])) / (n - 1)
    return float(y[-1]) + slope * _damped_steps(h)


def damped_trend(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Least-squares trend, damped, and anchored on the recent level.

    Anchoring on a short trailing mean rather than the fitted line's endpoint
    keeps a single noisy final observation from displacing the whole forecast.
    """
    y = np.asarray(train_y, dtype=float)
    n = len(y)
    if n < 4:
        return naive(train_y, h, freq)
    x = np.arange(n)
    slope = float(np.polyfit(x, y, 1)[0])
    # Anchor on a *short* trailing window. A mean is centred on the middle of
    # its window, so on a trending series a long window anchors the forecast
    # half a window in the past — with a 52-week season that is a six-month lag,
    # which is precisely how flat-mean models end up under-predicting a growing
    # series' totals. Short enough to track the current level, long enough not
    # to be moved by one noisy period.
    k = max(3, min(n, max(4, n // 8), season_length(freq, n)))
    anchor = _level(y[-k:])
    # The anchor represents the level ~k/2 periods ago; carry the trend forward
    # from there so step 1 starts at today's level rather than the window's.
    return anchor + slope * (k / 2.0) + slope * _damped_steps(h)


def holt_winters(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Exponential smoothing (trend + optional seasonal) via statsmodels."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    y = np.asarray(train_y, dtype=float)
    n = len(y)
    m = season_length(freq, n)
    seasonal = "add" if (m >= 2 and n >= 2 * m) else None
    trend = "add" if n >= 4 else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ExponentialSmoothing(
            y,
            trend=trend,
            seasonal=seasonal,
            seasonal_periods=m if seasonal else None,
            initialization_method="estimated",
        ).fit()
        fc = np.asarray(fit.forecast(h), dtype=float)
    return fc


def theta(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Theta method — the M3-competition winner: a robust decomposition of the
    series into long-run trend and short-run curvature. Excellent default."""
    from statsmodels.tsa.forecasting.theta import ThetaModel

    y = np.asarray(train_y, dtype=float)
    n = len(y)
    m = season_length(freq, n)
    seasonal = m if (m >= 2 and n >= 2 * m) else None
    s = pd.Series(y)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = ThetaModel(
            s, period=seasonal, deseasonalize=bool(seasonal), method="auto"
        ).fit()
        fc = np.asarray(res.forecast(h), dtype=float)
    return fc


def ets(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Error-Trend-Seasonal state-space exponential smoothing (damped trend)."""
    from statsmodels.tsa.exponential_smoothing.ets import ETSModel

    y = np.asarray(train_y, dtype=float)
    n = len(y)
    m = season_length(freq, n)
    seasonal = "add" if (m >= 2 and n >= 2 * m) else None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fit = ETSModel(
            y, error="add", trend="add", damped_trend=True,
            seasonal=seasonal, seasonal_periods=m if seasonal else None,
        ).fit(disp=False)
        fc = np.asarray(fit.forecast(h), dtype=float)
    return fc


# Small, curated (p,d,q) candidates — enough spread to fit a real variety of
# series shapes (pure AR, pure MA, mixed, already-stationary) without the
# runtime cost of an exhaustive grid or a full auto-ARIMA package.
_ARIMA_ORDERS: list[tuple[int, int, int]] = [
    (1, 1, 0), (0, 1, 1), (1, 1, 1), (2, 1, 1), (1, 1, 2), (2, 1, 0), (0, 1, 2),
    (1, 0, 0), (0, 0, 1),
]


def arima(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Auto-order ARIMA: fits a small curated (p,d,q) grid and keeps whichever
    converges with the lowest AIC — a lightweight stand-in for a full
    auto-ARIMA search, replacing a single arbitrary fixed order (1,1,1)."""
    from statsmodels.tsa.arima.model import ARIMA

    y = np.asarray(train_y, dtype=float)
    best_fit, best_aic = None, np.inf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for order in _ARIMA_ORDERS:
            try:
                fit = ARIMA(y, order=order).fit()
            except Exception:
                continue
            if np.isfinite(fit.aic) and fit.aic < best_aic:
                best_aic, best_fit = fit.aic, fit
        if best_fit is None:
            best_fit = ARIMA(y, order=(1, 1, 0)).fit()
        fc = np.asarray(best_fit.forecast(h), dtype=float)
    return fc


def sarima(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Seasonal ARIMA: a base ARIMA(1,1,1) plus a small curated seasonal-order
    grid at this series' natural period (7 for daily, 52 for weekly, 12 for
    monthly) — captures within-week / within-year seasonal patterns a plain
    ARIMA cannot. Only registered for series with enough history for at least
    two full seasonal cycles (see ``MODELS["SARIMA"]["min_n"]``)."""
    from statsmodels.tsa.statespace.sarimax import SARIMAX

    y = np.asarray(train_y, dtype=float)
    n = len(y)
    m = season_length(freq, n)
    best_fit, best_aic = None, np.inf
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for P, D, Q in [(1, 1, 0), (0, 1, 1), (1, 1, 1)]:
            try:
                fit = SARIMAX(
                    y, order=(1, 1, 1), seasonal_order=(P, D, Q, m),
                    enforce_stationarity=False, enforce_invertibility=False,
                ).fit(disp=False)
            except Exception:
                continue
            if np.isfinite(fit.aic) and fit.aic < best_aic:
                best_aic, best_fit = fit.aic, fit
        if best_fit is None:
            return arima(train_y, h, freq, train_dates)
        fc = np.asarray(best_fit.forecast(h), dtype=float)
    return fc


def seasonal_profile(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Robust level x multiplicative seasonal index.

    Estimates "what does a typical Friday look like relative to a typical day"
    as a median ratio against a centred rolling mean, then projects a robust
    recent level through that profile.  Medians make it shrug off the one-off
    spikes that wreck a mean-based seasonal estimate, and because it carries no
    trend it is the model of choice for a flat-but-seasonal series.
    """
    y = np.asarray(train_y, dtype=float)
    n = len(y)
    m = season_length(freq, n)
    if m < 2 or n < 2 * m:
        return moving_average(train_y, h, freq)

    # Centred rolling mean over exactly one season = the de-seasonalised level.
    level = pd.Series(y).rolling(m, center=True, min_periods=max(2, m // 2)).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(np.abs(level) > 1e-9, y / level, np.nan)

    idx = np.arange(n) % m
    profile = np.ones(m, dtype=float)
    for k in range(m):
        vals = ratio[idx == k]
        vals = vals[np.isfinite(vals)]
        if len(vals):
            profile[k] = float(np.median(vals))
    # Normalise so the profile redistributes the level rather than inflating it.
    mean_profile = float(np.mean(profile))
    if mean_profile > 1e-9:
        profile = profile / mean_profile

    # Mean, not median: the profile multiplies this level, so a level biased low
    # biases every horizon total low by the same proportion (see :func:`_level`).
    base = _level(y[-min(n, 4 * m):])
    future_idx = (np.arange(n, n + h)) % m
    return base * profile[future_idx]


def _fourier_design(t: np.ndarray, periods: list[float], n_hist: int) -> np.ndarray:
    """Design matrix: intercept, linear trend, and Fourier pairs per season."""
    cols = [np.ones_like(t, dtype=float), t.astype(float) / max(n_hist, 1)]
    for p in periods:
        # More harmonics for long cycles (a year has structure a single
        # sine cannot express); few for short ones, to avoid over-fitting.
        k_max = 10 if p > 100 else (3 if p > 20 else 3)
        k_max = max(1, min(k_max, int(p // 2)))
        for k in range(1, k_max + 1):
            ang = 2.0 * np.pi * k * t / p
            cols.append(np.sin(ang))
            cols.append(np.cos(ang))
    return np.column_stack(cols)


def harmonic_regression(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Ridge-regularised trend + multi-seasonal Fourier regression.

    The same decomposition Prophet performs (piecewise trend plus Fourier
    seasonality), solved as one small ridge problem instead of by MCMC — so it
    costs milliseconds rather than seconds and can therefore be honestly
    back-tested across every fold.  Unlike SARIMA it models *several* seasonal
    cycles at once, which is what daily retail data needs: a weekly trading
    rhythm sitting inside a yearly season.
    """
    y = np.asarray(train_y, dtype=float)
    n = len(y)
    periods = seasonal_periods(freq, n)
    if n < 8:
        return linear_trend(train_y, h, freq)

    t = np.arange(n, dtype=float)
    X = _fourier_design(t, periods, n)
    if X.shape[1] >= n:  # more parameters than data — fall back
        return linear_trend(train_y, h, freq)

    # Small ridge penalty for numerical stability; the intercept is left free.
    lam = 1e-3 * n
    penalty = np.eye(X.shape[1]) * lam
    penalty[0, 0] = 0.0
    try:
        beta = np.linalg.solve(X.T @ X + penalty, X.T @ y)
    except np.linalg.LinAlgError:
        return linear_trend(train_y, h, freq)

    t_future = np.arange(n, n + h, dtype=float)
    X_future = _fourier_design(t_future, periods, n)
    return X_future @ beta


def _stl_adjusted(y: np.ndarray, m: int):
    """STL decomposition -> (seasonally adjusted series, seasonal component)."""
    from statsmodels.tsa.seasonal import STL

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = STL(pd.Series(y), period=m, robust=True).fit()
    seasonal = np.asarray(res.seasonal, dtype=float)
    return y - seasonal, seasonal


def _stl_forecast(train_y, h, freq, inner) -> np.ndarray:
    """Forecast the seasonally-adjusted series, then re-attach the season."""
    y = np.asarray(train_y, dtype=float)
    n = len(y)
    m = season_length(freq, n)
    if m < 2 or n < 2 * m + 1:
        return inner(y, h, freq, None)

    adjusted, seasonal = _stl_adjusted(y, m)
    base = np.asarray(inner(adjusted, h, freq, None), dtype=float)
    last_season = seasonal[-m:]
    reps = int(np.ceil(h / m))
    return base + np.tile(last_season, reps)[:h]


def stl_ets(train_y, h, freq, train_dates=None) -> np.ndarray:
    """STL decomposition + ETS on the seasonally adjusted series.

    Hyndman's recommended treatment for series with a strong, stable season:
    STL isolates the seasonal shape far more robustly than ETS's own seasonal
    state, leaving the exponential smoother to do what it is best at — tracking
    level and trend.
    """
    return _stl_forecast(train_y, h, freq, lambda a, hh, f, d: ets(a, hh, "none", d))


def stl_arima(train_y, h, freq, train_dates=None) -> np.ndarray:
    """STL decomposition + auto-order ARIMA on the seasonally adjusted series."""
    return _stl_forecast(train_y, h, freq, lambda a, hh, f, d: arima(a, hh, "none", d))


def croston_sba(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Croston's method with the Syntetos-Boylan bias correction.

    For intermittent demand (lots of zero periods) the level of the series is
    the wrong thing to smooth — what matters is *how big* an order is when one
    arrives and *how often* one arrives.  Croston smooths those two separately;
    the 1 - alpha/2 factor removes the well-documented upward bias in the naive
    size/interval ratio.
    """
    y = np.asarray(train_y, dtype=float)
    nz = np.flatnonzero(y != 0)
    if len(nz) < 2:
        return np.full(h, float(np.mean(y)) if len(y) else 0.0, dtype=float)

    alpha = 0.1
    sizes = y[nz]
    intervals = np.diff(np.concatenate(([nz[0] + 1], nz + 1)))

    z = float(sizes[0])
    p = float(intervals[0]) if intervals[0] > 0 else 1.0
    for size, interval in zip(sizes[1:], intervals[1:]):
        z += alpha * (float(size) - z)
        p += alpha * (float(interval) - p)

    rate = (1.0 - alpha / 2.0) * z / max(p, 1e-9)
    return np.full(h, rate, dtype=float)


def prophet_point(train_y, h, freq, train_dates=None) -> np.ndarray:
    """Point forecast from a lightweight Prophet fit (no uncertainty sampling).

    Kept fast on purpose: the full interval-producing fit lives in
    ``engines.run_prophet`` and is only used once, for the chosen model.
    """
    from prophet import Prophet

    alias = freq_to_pandas(freq)
    if train_dates is not None:
        ds = pd.to_datetime(train_dates)
    else:
        ds = pd.date_range(end=pd.Timestamp.today().normalize(), periods=len(train_y), freq=alias)
    dfp = pd.DataFrame({"ds": ds, "y": np.asarray(train_y, dtype=float)})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = Prophet(
            uncertainty_samples=0,
            weekly_seasonality=(freq == "daily" and len(train_y) >= 14),
            yearly_seasonality=(freq != "daily" and len(train_y) >= 24),
            daily_seasonality=False,
        )
        model.fit(dfp)
    future = model.make_future_dataframe(periods=h, freq=alias)
    pred = model.predict(future)
    return pred["yhat"].to_numpy(dtype=float)[-h:]


# ── Registry ─────────────────────────────────────────────────────────────────

# name -> {fn, min_n (min history), needs (optional import module), cost}
#
# ``cost`` is a rough relative fit time used to budget the back-test: every
# model is re-fitted on every fold, so an expensive model multiplies run time by
# the fold count. "cheap" models are always back-tested; costly ones are only
# offered when the series is worth it (see ``candidate_models``).
MODELS: dict[str, dict] = {
    "Naive": {"fn": naive, "min_n": 2, "needs": None, "cost": "cheap"},
    "Seasonal Naive": {"fn": seasonal_naive, "min_n": 4, "needs": None, "cost": "cheap"},
    "Drift": {"fn": drift, "min_n": 3, "needs": None, "cost": "cheap"},
    "Moving Average": {"fn": moving_average, "min_n": 3, "needs": None, "cost": "cheap"},
    "Mean (2 seasons)": {"fn": moving_average_2x, "min_n": 6, "needs": None, "cost": "cheap"},
    "Mean (4 seasons)": {"fn": moving_average_4x, "min_n": 10, "needs": None, "cost": "cheap"},
    "Mean (long window)": {"fn": moving_average_long, "min_n": 12, "needs": None, "cost": "cheap"},
    "Linear Trend": {"fn": linear_trend, "min_n": 4, "needs": None, "cost": "cheap"},
    "Damped Drift": {"fn": damped_drift, "min_n": 4, "needs": None, "cost": "cheap"},
    "Damped Trend": {"fn": damped_trend, "min_n": 6, "needs": None, "cost": "cheap"},
    "Seasonal Profile": {"fn": seasonal_profile, "min_n": 8, "needs": None, "cost": "cheap"},
    "Harmonic Regression": {"fn": harmonic_regression, "min_n": 12, "needs": None, "cost": "cheap"},
    # Only offered for intermittent series: on a dense series Croston's flat
    # demand rate can post a competitive average error while carrying no level,
    # trend or seasonal information at all, and a tie-break on simplicity will
    # then hand it the run over a model that actually describes the data.
    "Croston": {
        "fn": croston_sba, "min_n": 8, "needs": None, "cost": "cheap",
        "intermittent_only": True,
    },
    "Holt-Winters": {"fn": holt_winters, "min_n": 8, "needs": "statsmodels", "cost": "medium"},
    "Theta": {"fn": theta, "min_n": 8, "needs": "statsmodels", "cost": "medium"},
    "ETS": {"fn": ets, "min_n": 10, "needs": "statsmodels", "cost": "medium"},
    "ARIMA": {"fn": arima, "min_n": 12, "needs": "statsmodels", "cost": "expensive"},
    "SARIMA": {"fn": sarima, "min_n": 20, "needs": "statsmodels", "cost": "expensive"},
    "STL-ETS": {"fn": stl_ets, "min_n": 20, "needs": "statsmodels", "cost": "expensive"},
    "STL-ARIMA": {"fn": stl_arima, "min_n": 20, "needs": "statsmodels", "cost": "expensive"},
    "Prophet": {"fn": prophet_point, "min_n": 24, "needs": "prophet", "cost": "expensive"},
}

# Fast models only — used to cap back-test cost when many metrics are requested,
# and to screen which target transform to use before the full search runs.
FAST_MODELS = [
    "Naive", "Seasonal Naive", "Drift", "Moving Average", "Mean (4 seasons)",
    "Linear Trend", "Seasonal Profile", "Harmonic Regression",
]

# Models whose forecast is a flat demand *rate* rather than a level path — the
# only sensible family for intermittent series, where trend/seasonal models fit
# the zeros instead of the demand.
INTERMITTENT_MODELS = ["Croston", "Naive", "Moving Average", "Seasonal Profile"]


def get_model_fn(name: str) -> Callable | None:
    spec = MODELS.get(name)
    return spec["fn"] if spec else None


def is_available(name: str, n: int) -> bool:
    spec = MODELS.get(name)
    if not spec:
        return False
    return n >= spec["min_n"] and _has(spec["needs"])


def available_models(n: int, freq: str) -> list[str]:
    """Candidate models usable for ``n`` observations with installed deps."""
    return [name for name in MODELS if is_available(name, n)] or ["Naive"]


_COST_ORDER = {"cheap": 0, "medium": 1, "expensive": 2}


def candidate_models(
    n: int,
    freq: str,
    intermittent: bool = False,
    max_cost: str = "expensive",
) -> list[str]:
    """The candidate set for this particular series.

    Three things narrow it down:

    * **History** — a model that needs two seasonal cycles cannot be judged on
      one, so it is not offered rather than being offered and failing.
    * **Shape** — an intermittent series is handed to demand-rate models only.
      Fitting ETS or ARIMA to a column that is mostly zeros produces a
      confident-looking forecast of the *zeros*, not of demand.
    * **Budget** — every candidate is re-fitted on every back-test fold, so the
      expensive families are dropped when the caller is running many metrics
      and needs the answer back in seconds.
    """
    if intermittent:
        pool = [m for m in INTERMITTENT_MODELS if is_available(m, n)]
        return pool or ["Naive"]

    ceiling = _COST_ORDER.get(max_cost, 2)
    pool = [
        name for name, spec in MODELS.items()
        if is_available(name, n)
        and not spec.get("intermittent_only")
        and _COST_ORDER.get(spec.get("cost", "cheap"), 0) <= ceiling
    ]
    return pool or ["Naive"]
