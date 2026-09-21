"""
Rolling-origin (expanding-window) back-testing and model selection.

Each candidate is re-fitted on an expanding training window and scored on the
held-out next ``h`` points across several folds.  This is what makes the
``Auto`` selector honest — the winner is the model that actually generalises,
not the one that fits the training data best.

Three properties this module is responsible for, each of which was a real
source of error before:

**Causality.**  Everything a fold does to its training window — anomaly repair,
transforms, the model fit — sees only data from before the fold's cut point.
Repairing outliers with a *centred* rolling median (the previous behaviour, and
applied to the whole series before back-testing) leaks the future into the
past, which makes back-test scores look better than the forecast will be.

**Robust ranking.**  Ranking on error pooled across folds lets a single bad
period decide the winner: this project's own data has a week with a 97% revenue
drop caused by missing rows, and pooled MASE hands the run to whichever model
happened to undershoot that week.  Candidates are ranked by their *median rank
across folds* instead, so a model has to be good repeatedly, not once.

**Parsimony.**  With 3-6 folds, differences of a few percent in back-test error
are noise, and picking the nominal winner is selection over-fitting.  A
one-standard-error rule keeps the simplest model that is statistically
indistinguishable from the best one.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np

from agents.forecasting.tools.anomalies import repair as repair_anomalies
from agents.forecasting.tools.conformal import ResidualBank
from agents.forecasting.tools.evaluation import (
    evaluate_forecast,
    naive_scale,
    squared_naive_scale,
)
from agents.forecasting.tools.models import (
    MODELS,
    available_models,
    season_length,
)
from agents.forecasting.tools.transform import (
    IDENTITY,
    Transform,
    candidate_transforms,
    wrap,
)

# The most relative error a simpler model may cost before parsimony stops being
# worth it (see :func:`_one_se_pick`).
_TIE_MARGIN = 0.04

# How much better a transformed fit must be before the untransformed scale is
# abandoned. Transforming is not free — it adds a back-transform bias
# correction and makes the fit harder to reason about — so it should win on
# evidence, not on a coin flip.
_TRANSFORM_MARGIN = 0.02

# Model complexity for the one-standard-error rule: when two candidates are
# statistically tied, the lower-complexity one is the safer bet out of sample.
_COMPLEXITY = {
    "Naive": 0, "Moving Average": 1, "Seasonal Naive": 1, "Drift": 1,
    "Mean (2 seasons)": 1, "Mean (4 seasons)": 1, "Mean (long window)": 1,
    "Damped Drift": 2, "Damped Trend": 2,
    "Linear Trend": 2, "Seasonal Profile": 2, "Croston": 2,
    "Theta": 3, "Holt-Winters": 4, "ETS": 4,
    "Harmonic Regression": 5, "ARIMA": 6, "STL-ETS": 6,
    "SARIMA": 7, "STL-ARIMA": 7, "Prophet": 8, "Ensemble": 9,
}


def _splits(n: int, h: int, folds: int, min_train: int) -> list[tuple[int, int]]:
    """Expanding-window (test_start, test_end) index pairs, oldest fold first."""
    splits: list[tuple[int, int]] = []
    for k in range(folds, 0, -1):
        test_start = n - k * h
        if test_start < min_train:
            continue
        splits.append((test_start, min(test_start + h, n)))
    return splits


def _min_train(n: int, h: int, freq: str) -> int:
    # Enough history to fit one seasonal cycle, but never so large that no fold
    # can form — capping at n//2 lets typical weekly/monthly series (season 52/12)
    # still be back-tested instead of silently falling back to Naive.
    m = season_length(freq, n)
    return max(min(2 * m, n // 2), 8, h)


def _resolve_folds(n: int, h: int, freq: str, folds: int) -> tuple[int, int]:
    """Reduce fold count until at least one valid split exists."""
    min_train = _min_train(n, h, freq)
    while folds > 1 and (folds * h + min_train) > n:
        folds -= 1
    return folds, min_train


# Which scaled error a leaderboard is ranked on, and the per-fold key that
# backs it. See :func:`objective_for` for why the choice matters.
_OBJECTIVES = {
    "mase": ("mase", "fold_mase", "mae"),
    "rmsse": ("rmsse", "fold_rmsse", "rmse"),
}


def objective_for(aggregation: str) -> str:
    """The error metric a series should be ranked on, given what it measures.

    * **Additive metrics** (revenue, units, orders) are published as a horizon
      **total**. A total is a sum of means, and the mean is what minimises
      *squared* error — so these rank on **RMSSE**, the M5 competition's metric,
      chosen there for exactly this reason.
    * **Rates** (average order value, discount %) are published as a typical
      level, where a median-like central estimate is more representative and
      far less exposed to a single outlying period. Those rank on **MASE**.

    Getting this backwards is not a subtle loss of accuracy: ranking a
    right-skewed revenue series on MASE selects for the conditional median and
    under-states every horizon total by a consistent margin.
    """
    return "rmsse" if aggregation == "sum" else "mase"


# How much weight the horizon-total error carries in the ranked score for an
# additive metric. At 1.0 a model that misses the total by 30% is ranked as if
# its per-period error were 30% worse — enough to matter, not enough to let a
# single lucky total override consistently better period-level accuracy.
_AGG_WEIGHT = 1.0


def _primary_error(r: dict) -> float | None:
    """The value a leaderboard entry is ranked on, with graceful degradation.

    For additive metrics this is the per-period scaled error **inflated by the
    relative error on the horizon total**. Ranking on per-period error alone
    quietly optimises for the wrong thing: a flat mean model wins on squared
    per-period error precisely because it ignores trend, and then misses the
    30-day total that trend determines. Both are what we ship, so both are what
    we rank on.
    """
    key, _, fallback = _OBJECTIVES.get(r.get("objective", "mase"), _OBJECTIVES["mase"])
    base = None
    for candidate in (key, "mase", "rmsse", fallback, "mae"):
        v = r.get(candidate)
        if v is not None:
            base = float(v)
            break
    if base is None:
        return None
    if r.get("objective") == "rmsse" and r.get("agg_ape") is not None:
        base *= 1.0 + _AGG_WEIGHT * float(r["agg_ape"])
    return base


def _sort_key(r: dict) -> tuple[float, float, float, float]:
    """Order by expected out-of-sample error, with consistency as the tie-break.

    The leading term is the scaled error chosen by :func:`objective_for` —
    scale-free, and unlike MAPE it does not explode when actuals pass near zero.
    ``rank_score`` — the median of the model's per-fold ranks — breaks ties, so
    between two models with the same error the one that was good *repeatedly*
    wins over the one that was rescued by a single lucky fold.

    Consistency is deliberately not the primary key: as a first sort it demotes
    genuinely more accurate models for being uneven, which is how a plain
    ``Naive`` forecast came to outrank a materially better ``Seasonal Naive``.
    Robustness against one bad fold belongs in the tie test (:func:`_is_tied_with`),
    which compares models on the same folds instead of on their marginals.
    """
    inf = float("inf")
    primary = _primary_error(r)
    return (
        primary if primary is not None else inf,
        r.get("rank_score") if r.get("rank_score") is not None else inf,
        r.get("mape") if r.get("mape") is not None else inf,
        r.get("rmse") if r.get("rmse") is not None else inf,
    )


def _backtest_predictor(
    predict_fn,
    y,
    dates,
    h: int,
    freq: str,
    folds: int,
    scale: float | None = None,
    sq_scale: float | None = None,
    collect_residuals: bool = False,
    repair: bool = True,
) -> dict | None:
    """Shared expanding-window scoring loop.

    ``predict_fn(train_y, h_step, freq, train_dates) -> np.ndarray | None`` — a
    prediction source, which may be a single model or a blend of several.
    Returns ``None`` if any fold fails or produces a non-finite/short forecast.

    Scoring always uses the **raw** held-out actuals; only the training window
    is repaired, so a model is never rewarded for a period we smoothed away.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    folds, min_train = _resolve_folds(n, h, freq, folds)
    actuals: list[np.ndarray] = []
    preds: list[np.ndarray] = []
    per_fold: list[dict] = []
    agg_errors: list[float] = []
    bank = ResidualBank() if collect_residuals else None

    for ts, te in _splits(n, h, folds, min_train):
        train_y = y[:ts]
        if repair and len(train_y) >= 12:
            train_y = repair_anomalies(train_y, freq=freq)
        train_d = dates[:ts] if dates is not None else None
        p = predict_fn(train_y, te - ts, freq, train_d)
        if p is None or len(p) < (te - ts) or not np.all(np.isfinite(p[: te - ts])):
            return None
        a = y[ts:te]
        p = np.asarray(p, dtype=float)[: te - ts]
        actuals.append(a)
        preds.append(p)
        per_fold.append(evaluate_forecast(a, p, scale=scale, sq_scale=sq_scale))
        denom = abs(float(np.sum(a)))
        agg_errors.append(
            (float(np.sum(p)) - float(np.sum(a))) / denom if denom > 1e-9 else np.nan
        )
        if bank is not None:
            bank.add(a, p)

    if not actuals:
        return None

    a = np.concatenate(actuals)
    p = np.concatenate(preds)
    metrics = evaluate_forecast(a, p, scale=scale, sq_scale=sq_scale)
    metrics["residual_std"] = round(float(np.std(a - p)), 4)
    metrics["folds"] = len(actuals)
    metrics["fold_mase"] = [f.get("mase") for f in per_fold]
    metrics["fold_rmsse"] = [f.get("rmsse") for f in per_fold]
    metrics["fold_mae"] = [f.get("mae") for f in per_fold]

    # How far the *horizon total* landed from the truth in each fold. This is
    # the figure the product publishes, and a model can score well per period
    # while drifting badly on the sum (or vice versa), so it is measured
    # explicitly rather than inferred.
    finite = [e for e in agg_errors if e is not None and np.isfinite(e)]
    metrics["agg_bias"] = round(float(np.mean(finite)), 4) if finite else None
    metrics["agg_ape"] = round(float(np.mean(np.abs(finite))), 4) if finite else None
    if bank is not None:
        metrics["_bank"] = bank
    return metrics


def backtest_model(
    model_fn,
    y,
    dates,
    h: int,
    freq: str,
    folds: int = 3,
    scale: float | None = None,
    sq_scale: float | None = None,
    collect_residuals: bool = False,
    repair: bool = True,
) -> dict | None:
    """Average out-of-sample metrics for one model. ``None`` if it cannot run."""

    def predict(train_y, h_step, freq_, train_d):
        try:
            return model_fn(train_y, h_step, freq_, train_d)
        except Exception:
            return None

    return _backtest_predictor(
        predict, y, dates, h, freq, folds, scale=scale, sq_scale=sq_scale,
        collect_residuals=collect_residuals, repair=repair,
    )


def backtest_ensemble(
    components: list[str],
    weights: list[float],
    y,
    dates,
    h: int,
    freq: str,
    folds: int = 3,
    scale: float | None = None,
    sq_scale: float | None = None,
    transform: Transform | None = None,
    collect_residuals: bool = False,
    repair: bool = True,
) -> dict | None:
    """Back-test a weighted blend of ``components`` the same way as a single model.

    Each fold re-fits every component on the same expanding window and combines
    their predictions with ``weights`` *before* scoring — so the reported error
    is the honest out-of-sample error of the blend itself, not an average of the
    components' individual scores.
    """
    tr = transform or IDENTITY
    fns = [wrap(MODELS[c]["fn"], tr) for c in components if c in MODELS]
    if len(fns) != len(components) or len(fns) < 2:
        return None
    w = np.asarray(weights, dtype=float)

    def predict(train_y, h_step, freq_, train_d):
        sub_preds = []
        for fn in fns:
            try:
                p = fn(train_y, h_step, freq_, train_d)
            except Exception:
                return None
            if p is None:
                return None
            p = np.asarray(p, dtype=float)
            if len(p) < h_step or not np.all(np.isfinite(p[:h_step])):
                return None
            sub_preds.append(p[:h_step])
        return np.average(np.stack(sub_preds, axis=0), axis=0, weights=w)

    return _backtest_predictor(
        predict, y, dates, h, freq, folds, scale=scale, sq_scale=sq_scale,
        collect_residuals=collect_residuals, repair=repair,
    )


def inverse_error_weights(scores: list[float]) -> list[float]:
    """Normalise inverse-error weights (lower error -> more weight in the blend)."""
    eps = 1e-6
    inv = [1.0 / max(float(s), eps) for s in scores]
    total = sum(inv)
    return [round(w / total, 4) for w in inv]


def _assign_rank_scores(leaderboard: list[dict]) -> None:
    """Attach the median per-fold rank to every entry, in place.

    Pooled error answers "who had the lowest total error"; median rank answers
    "who is reliably good", which is the question that survives one broken week
    in the data.
    """
    if not leaderboard:
        return
    n_folds = max(len(_paired_fold_scores(r)) for r in leaderboard)
    if n_folds == 0:
        for r in leaderboard:
            r["rank_score"] = None
        return

    ranks: dict[int, list[float]] = {i: [] for i in range(len(leaderboard))}
    for f in range(n_folds):
        scores = []
        for i, r in enumerate(leaderboard):
            fold_scores = _paired_fold_scores(r)
            v = fold_scores[f] if f < len(fold_scores) and fold_scores[f] is not None else None
            scores.append((float("inf") if v is None else float(v), i))
        for position, (_, i) in enumerate(sorted(scores)):
            ranks[i].append(position + 1.0)

    for i, r in enumerate(leaderboard):
        r["rank_score"] = round(float(np.median(ranks[i])), 3) if ranks[i] else None


def _paired_fold_scores(entry: dict) -> list[float | None]:
    _, fold_key, _ = _OBJECTIVES.get(entry.get("objective", "mase"), _OBJECTIVES["mase"])
    return list(entry.get(fold_key) or entry.get("fold_mase") or entry.get("fold_mae") or [])


def _is_tied_with(candidate: dict, best: dict) -> bool:
    """Is ``candidate`` statistically indistinguishable from ``best``?

    Compared **fold by fold**, which is the whole point. Judging each model
    against its own marginal spread is far too weak here: business series have
    easy quarters and hard ones, so every model's fold-to-fold variance is large
    and swamps the differences *between* models. That made every candidate look
    tied with every other and handed the run to the simplest model on the board
    — which is how a `Naive` forecast ended up beating a genuinely better
    `Seasonal Naive` on this project's monthly data.

    Pairing removes the shared difficulty of each fold, so what is tested is the
    only thing that matters: whether this model is consistently worse on the
    *same* data.
    """
    a = _paired_fold_scores(candidate)
    b = _paired_fold_scores(best)
    pairs = [
        (float(x), float(y)) for x, y in zip(a, b)
        if x is not None and y is not None and np.isfinite(x) and np.isfinite(y)
    ]
    if len(pairs) < 3:
        # Too few folds for a paired test — fall back to requiring the candidate
        # to be within a few percent of the best.
        ca, cb = _primary_error(candidate), _primary_error(best)
        if ca is None or cb is None or cb <= 0:
            return False
        return float(ca) <= float(cb) * 1.03

    diff = np.asarray([x - y for x, y in pairs], dtype=float)
    if np.allclose(diff, 0.0):
        return True
    se = float(np.std(diff, ddof=1)) / np.sqrt(len(diff))
    if se <= 1e-12:
        return bool(np.mean(diff) <= 0)
    # Within one standard error of "no difference".
    return bool(float(np.mean(diff)) <= se)


def _one_se_pick(leaderboard: list[dict]) -> dict:
    """Simplest model that is not distinguishably worse than the best.

    With a handful of folds a 3% difference in back-test error is well inside
    the noise, and taking the nominal winner fits the selection procedure to
    that noise. This keeps the least complex candidate that a paired comparison
    cannot separate from the leader.
    """
    best = leaderboard[0]
    best_err = _primary_error(best)
    if best_err is None:
        return best

    # Hard ceiling on what simplicity may cost. The paired test alone is not
    # enough: on a short back-test it will call a 20%-worse model "not
    # distinguishable", and trading away a real 20% of accuracy for a tidier
    # model is not parsimony, it is a worse forecast. A candidate has to be both
    # statistically tied *and* within a few percent to qualify.
    ceiling = float(best_err) * (1.0 + _TIE_MARGIN)

    def within_margin(r: dict) -> bool:
        err = _primary_error(r)
        return err is not None and float(err) <= ceiling

    tied = [r for r in leaderboard if r is best or (within_margin(r) and _is_tied_with(r, best))]
    if not tied:
        return best
    return min(tied, key=lambda r: (_COMPLEXITY.get(r["model"], 5), _sort_key(r)))


def select_transform(
    y,
    dates,
    h: int,
    freq: str,
    folds: int = 3,
    screen_models: list[str] | None = None,
    objective: str = "mase",
) -> Transform:
    """Pick the target transform by back-testing a cheap screening set.

    Whether a series is additive or multiplicative is a property of the
    *series*, not of any one model, so it is decided once against a few fast
    models rather than multiplying the full search by the number of transforms.
    Errors are always scored on the original scale, so the comparison is fair.
    """
    y = np.asarray(y, dtype=float)
    options = candidate_transforms(y)
    if len(options) <= 1:
        return IDENTITY

    from agents.forecasting.tools.models import FAST_MODELS, is_available

    names = [m for m in (screen_models or FAST_MODELS) if is_available(m, len(y))]
    if not names:
        return IDENTITY

    m = season_length(freq, len(y))
    scale, sq_scale = naive_scale(y, m=m), squared_naive_scale(y, m=m)
    # Screen on the same loss the models will be ranked on, so the transform is
    # not chosen to help a criterion we then discard.
    err_key = "rmse" if objective == "rmsse" else "mae"

    def _screen(pair):
        tr_, name = pair
        return tr_, backtest_model(
            wrap(MODELS[name]["fn"], tr_), y, dates, h, freq, folds,
            scale=scale, sq_scale=sq_scale,
        )

    pairs = [(tr, name) for tr in options for name in names]
    with ThreadPoolExecutor(max_workers=min(len(pairs), 4)) as pool:
        screened = list(pool.map(_screen, pairs))

    best_tr, best_score = IDENTITY, float("inf")
    for tr in options:
        scores = [
            float(m[err_key]) for t_, m in screened
            if t_ is tr and m and m.get(err_key) is not None
        ]
        if not scores:
            continue
        # Score each transform by the *best* error it makes achievable, not the
        # median across the screening set. The median asks "does this help every
        # model", which is the wrong question: the run will use the best model,
        # so the transform that matters is the one that improves that model.
        # Measured on a strongly multiplicative series, a log improved Linear
        # Trend and Harmonic Regression — the two candidates actually in
        # contention — by ~4%, while leaving Naive and Drift (RMSE ~25% worse,
        # and never selectable) unchanged. The median cancelled the real gain
        # against the irrelevant models and reported "no benefit".
        score = float(np.min(scores))
        # Require a clear margin before leaving the untransformed scale, so a
        # transform is never adopted on a coin-flip difference.
        if tr is IDENTITY:
            score *= 1.0 - _TRANSFORM_MARGIN
        if score < best_score:
            best_tr, best_score = tr, score
    return best_tr


def select_model(
    y,
    dates,
    h: int,
    freq: str,
    candidates: list[str] | None = None,
    folds: int = 3,
    ensemble_size: int = 3,
    transform: Transform | None = None,
    repair: bool = True,
    objective: str = "mase",
) -> list[dict]:
    """Back-test candidates and return a leaderboard, best first.

    Each entry: ``{model, mae, rmse, mape, mase, rank_score, residual_std,
    folds, fold_mase}``.  When at least two individual candidates back-test
    successfully, an inverse-error-weighted **Ensemble** of the top
    ``ensemble_size`` performers is also back-tested and added as its own
    candidate — it only outranks a single model if it genuinely has a lower
    out-of-sample error, keeping the ``Auto`` selector honest.
    """
    y = np.asarray(y, dtype=float)
    n = len(y)
    if candidates is None:
        candidates = available_models(n, freq)
    tr = transform or IDENTITY

    # One MASE scale for the whole leaderboard, computed from the full training
    # series' own seasonal-naive difference — every candidate is scored against
    # the same denominator so their MASE values stay directly comparable.
    m = season_length(freq, n)
    scale, sq_scale = naive_scale(y, m=m), squared_naive_scale(y, m=m)

    # Candidates are independent, and the expensive ones (SARIMA, STL-ARIMA,
    # Prophet) spend nearly all their time inside statsmodels/Stan routines that
    # release the GIL — so fitting them concurrently is close to free. Sequential
    # evaluation made a single metric take ~20s of wall clock for ~9s of work.
    named = [(name, MODELS[name]) for name in candidates if name in MODELS]

    def _score(item):
        name, spec = item
        metrics = backtest_model(
            wrap(spec["fn"], tr), y, dates, h, freq, folds,
            scale=scale, sq_scale=sq_scale, repair=repair,
        )
        return name, metrics

    leaderboard: list[dict] = []
    if len(named) > 1:
        with ThreadPoolExecutor(max_workers=min(len(named), 4)) as pool:
            results = list(pool.map(_score, named))
    else:
        results = [_score(item) for item in named]

    for name, metrics in results:
        if metrics and metrics.get("mae") is not None:
            leaderboard.append({
                "model": name, "transform": tr.name, "objective": objective, **metrics
            })

    _assign_rank_scores(leaderboard)
    leaderboard.sort(key=_sort_key)

    if len(leaderboard) >= 2:
        top = leaderboard[:ensemble_size]
        names = [r["model"] for r in top]
        weights = inverse_error_weights([_primary_error(r) or 1.0 for r in top])
        ens_metrics = backtest_ensemble(
            names, weights, y, dates, h, freq, folds,
            scale=scale, sq_scale=sq_scale, transform=tr, repair=repair,
        )
        if ens_metrics and ens_metrics.get("mae") is not None:
            leaderboard.append({
                "model": "Ensemble", "transform": tr.name, "objective": objective,
                "components": names, "weights": weights, **ens_metrics
            })
            _assign_rank_scores(leaderboard)
            leaderboard.sort(key=_sort_key)

    return leaderboard


def pick_winner(leaderboard: list[dict]) -> dict | None:
    """Apply the one-standard-error rule to a sorted leaderboard."""
    if not leaderboard:
        return None
    return _one_se_pick(leaderboard)


def skill_vs_naive(leaderboard: list[dict], selected: str) -> float | None:
    """Relative error improvement of the selected model over the best naive baseline.

    ``0.42`` means 42% lower error than the baseline; negative means worse.

    Two details keep this from flattering the result. The comparison is against
    the *stronger* of the plain and seasonal naive models, so "beats naive"
    cannot be claimed by clearing the easier bar on a seasonal series. And it is
    measured on the same loss the model was selected under, so a model cannot
    be chosen on one criterion and then credited with skill on another.
    """
    by_model = {r["model"]: r for r in leaderboard}
    sel = by_model.get(selected)
    if not sel:
        return None
    sel_err = _primary_error(sel)
    if sel_err is None:
        return None

    baselines = [
        _primary_error(by_model[m])
        for m in ("Naive", "Seasonal Naive", "Moving Average")
        if m in by_model and _primary_error(by_model[m]) is not None
    ]
    if not baselines:
        return None
    best_baseline = min(float(b) for b in baselines)
    if best_baseline <= 0:
        return None
    return round(1.0 - float(sel_err) / best_baseline, 3)
