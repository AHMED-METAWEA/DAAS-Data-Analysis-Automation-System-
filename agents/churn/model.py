"""
Churn model — production pipeline.

Labelling (no explicit churn column needed):
  cutoff   = a point in the past
  features = each customer's behaviour up to `cutoff`
  label    = 1 (churned) if the customer did NOT purchase in (cutoff, cutoff+horizon]

Production hardening over the original single-snapshot version:

  * **Multi-cutoff training panel** — features/labels are built at several
    historical cutoffs and stacked, so the model sees behaviour across
    different periods instead of memorising one snapshot (more rows, more
    robust on small *and* seasonal data).
  * **Out-of-time validation** — the newest cutoff is held out entirely;
    metrics measure how well the model predicts a *future* window, which is
    exactly the production task. Random splits (the old approach) leak
    period-specific patterns and overstate quality.
  * **HistGradientBoostingClassifier** — histogram-based boosting scales to
    hundreds of thousands of customers, handles NaN natively and supports
    early stopping.
  * **Post-hoc probability calibration** on the held-out window (sigmoid),
    so a score of 0.7 means ≈70% observed churn risk.
  * **Permutation importance** on the held-out window — honest "what drives
    churn" attribution (impurity importances overstate high-cardinality
    features).

A recency heuristic remains as a graceful fallback for tiny datasets.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .features import compute_customer_features, feature_columns

MIN_TRAIN_ROWS = 40          # minimum stacked panel rows for a supervised model
MIN_VALID_ROWS = 10          # minimum held-out rows for out-of-time metrics
MAX_CUTOFFS = 6              # cap on the number of historical training cutoffs
MIN_PRE_HISTORY_DAYS = 14    # a cutoff needs at least this much history before it


def _label_churn(
    tx: pd.DataFrame, cutoff: pd.Timestamp, horizon_end: pd.Timestamp, schema: dict
) -> pd.DataFrame:
    """Features as-of ``cutoff`` + churn label over (cutoff, horizon_end]."""
    cust = schema["customer_column"]
    pre = tx[tx["_dt"] <= cutoff]
    feats = compute_customer_features(pre, cutoff, schema)
    post = tx[(tx["_dt"] > cutoff) & (tx["_dt"] <= horizon_end)]
    active_after = set(post[cust].unique())
    feats["churned"] = (~feats.index.isin(active_after)).astype(int)
    return feats


def _training_cutoffs(
    tx: pd.DataFrame, snapshot: pd.Timestamp, horizon: int
) -> list[pd.Timestamp]:
    """Historical cutoffs, newest first. Each must leave a full outcome window
    before the snapshot and enough pre-history to compute features."""
    first = tx["_dt"].min()
    newest = snapshot - pd.Timedelta(days=horizon)
    stride = max(7, horizon // 2)
    cutoffs: list[pd.Timestamp] = []
    c = newest
    while len(cutoffs) < MAX_CUTOFFS and c >= first + pd.Timedelta(days=MIN_PRE_HISTORY_DAYS):
        cutoffs.append(c)
        c = c - pd.Timedelta(days=stride)
    return cutoffs


def train_and_predict(
    tx: pd.DataFrame,
    snapshot: pd.Timestamp,
    horizon: int,
    schema: dict,
    random_state: int = 42,
) -> dict | None:
    """Train on a multi-cutoff panel, validate out-of-time, score all customers.

    Returns ``None`` when the data can't support a supervised model
    (too little history/rows or a single class), so the caller can fall back.
    """
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    cutoffs = _training_cutoffs(tx, snapshot, horizon)
    if not cutoffs:
        return None

    # Newest cutoff = out-of-time validation window; the rest train the model.
    panels = []
    for c in cutoffs:
        p = _label_churn(tx, c, c + pd.Timedelta(days=horizon), schema)
        p["_cutoff"] = c
        panels.append(p)

    valid_panel = panels[0]
    train_panels = panels[1:]

    validation = "out_of_time"
    if not train_panels or sum(len(p) for p in train_panels) < MIN_TRAIN_ROWS:
        # Too little history for separate windows — fall back to a random
        # split on the newest cutoff (still honestly labelled, just weaker).
        validation = "random_split"
        full = valid_panel
        if len(full) < MIN_TRAIN_ROWS or full["churned"].nunique() < 2:
            return None
        from sklearn.model_selection import train_test_split

        strat = full["churned"] if full["churned"].value_counts().min() >= 2 else None
        train_df, valid_df = train_test_split(
            full, test_size=0.25, random_state=random_state, stratify=strat
        )
    else:
        train_df = pd.concat(train_panels)
        valid_df = valid_panel
        if (
            train_df["churned"].nunique() < 2
            or len(valid_df) < MIN_VALID_ROWS
            or valid_df["churned"].nunique() < 2
        ):
            # Degenerate windows — try the single-cutoff random split path.
            return train_and_predict_single(
                tx, snapshot, horizon, schema, random_state=random_state
            )

    cols = feature_columns(train_df)
    if not cols:
        return None

    X_tr = train_df[cols].to_numpy(dtype=float)
    y_tr = train_df["churned"].to_numpy(dtype=int)
    X_va = valid_df[cols].to_numpy(dtype=float)
    y_va = valid_df["churned"].to_numpy(dtype=int)

    model = HistGradientBoostingClassifier(
        random_state=random_state,
        max_iter=300,
        learning_rate=0.08,
        max_depth=None,
        min_samples_leaf=max(5, len(X_tr) // 200),
        early_stopping="auto",
    )
    model.fit(X_tr, y_tr)

    proba_va = model.predict_proba(X_va)[:, 1]
    pred_va = (proba_va >= 0.5).astype(int)
    auc = float(roc_auc_score(y_va, proba_va)) if len(np.unique(y_va)) > 1 else None

    # ── Calibration on the held-out window ──────────────────────────────────
    # Sigmoid calibration is monotonic: ranking and AUC are unchanged, only
    # the probability values become trustworthy.
    scorer = model
    calibrated = False
    try:
        if int(np.bincount(y_va).min()) >= 15:
            from sklearn.calibration import CalibratedClassifierCV
            from sklearn.frozen import FrozenEstimator

            cal = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid")
            cal.fit(X_va, y_va)
            scorer = cal
            calibrated = True
    except Exception:
        scorer = model

    metrics = {
        "name": "HistGradientBoosting",
        "validation": validation,
        "n_cutoffs": len(cutoffs),
        "auc": round(auc, 3) if auc is not None else None,
        "accuracy": round(float(accuracy_score(y_va, pred_va)), 3),
        "precision": round(float(precision_score(y_va, pred_va, zero_division=0)), 3),
        "recall": round(float(recall_score(y_va, pred_va, zero_division=0)), 3),
        "f1": round(float(f1_score(y_va, pred_va, zero_division=0)), 3),
        "base_churn_rate": round(float(np.concatenate([y_tr, y_va]).mean()), 3),
        "n_train": int(len(y_tr)),
        "n_test": int(len(y_va)),
        "confusion_matrix": confusion_matrix(y_va, pred_va).tolist(),
        "threshold": 0.5,
        "calibrated": calibrated,
        # Honest provenance: calibration is fit on the (only) out-of-time
        # window, so the SHIPPED probabilities are calibrated, but the
        # operating-point metrics above are the UNCALIBRATED model at 0.5 —
        # they describe ranking quality (AUC is calibration-invariant), not the
        # calibrated probability values. Spelled out so narration/UI can't
        # overstate what the numbers mean.
        "calibration": {
            "applied": calibrated,
            "method": "sigmoid" if calibrated else None,
            "fit_on": "out_of_time_validation_window" if calibrated else None,
            "metrics_basis": "uncalibrated_model_threshold_0.5",
            "note": (
                "Probabilities are sigmoid-calibrated on the held-out validation "
                "window (ranking/AUC unchanged); precision/recall/F1/confusion "
                "reflect the uncalibrated model at threshold 0.5."
                if calibrated else
                "Not calibrated (too few positives in the validation window for a "
                "reliable fit); probabilities are the model's raw estimates."
            ),
        },
    }

    importances = _permutation_importances(
        model, X_va, y_va, cols, random_state=random_state
    )
    shap_importance = _shap_global_importance(model, X_va, cols, random_state=random_state)

    # ── Live scoring: features as-of the snapshot for every customer ────────
    live = compute_customer_features(tx, snapshot, schema)
    live_proba = scorer.predict_proba(live[cols].to_numpy(dtype=float))[:, 1]
    live = live.copy()
    live["churn_probability"] = np.round(live_proba, 4)

    return {
        "metrics": metrics,
        "importances": importances,
        "shap_importance": shap_importance,
        "scored": live,
        "feature_cols": cols,
        "cutoff": cutoffs[0],
        "training_cutoffs": [str(c.date()) for c in cutoffs],
        "_model": model,
    }


def train_and_predict_single(
    tx: pd.DataFrame,
    snapshot: pd.Timestamp,
    horizon: int,
    schema: dict,
    random_state: int = 42,
) -> dict | None:
    """Single-cutoff random-split path for short histories."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score
    from sklearn.model_selection import train_test_split

    cutoff = snapshot - pd.Timedelta(days=horizon)
    feats = _label_churn(tx, cutoff, snapshot, schema)
    cols = feature_columns(feats)
    if not cols or len(feats) < MIN_TRAIN_ROWS or feats["churned"].nunique() < 2:
        return None

    strat = feats["churned"] if feats["churned"].value_counts().min() >= 2 else None
    train_df, valid_df = train_test_split(
        feats, test_size=0.25, random_state=random_state, stratify=strat
    )
    X_tr = train_df[cols].to_numpy(dtype=float)
    y_tr = train_df["churned"].to_numpy(dtype=int)
    X_va = valid_df[cols].to_numpy(dtype=float)
    y_va = valid_df["churned"].to_numpy(dtype=int)

    model = HistGradientBoostingClassifier(random_state=random_state, max_iter=200)
    model.fit(X_tr, y_tr)
    proba_va = model.predict_proba(X_va)[:, 1]
    auc = float(roc_auc_score(y_va, proba_va)) if len(np.unique(y_va)) > 1 else None

    from sklearn.metrics import (
        accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )
    pred_va = (proba_va >= 0.5).astype(int)
    metrics = {
        "name": "HistGradientBoosting",
        "validation": "random_split",
        "n_cutoffs": 1,
        "auc": round(auc, 3) if auc is not None else None,
        "accuracy": round(float(accuracy_score(y_va, pred_va)), 3),
        "precision": round(float(precision_score(y_va, pred_va, zero_division=0)), 3),
        "recall": round(float(recall_score(y_va, pred_va, zero_division=0)), 3),
        "f1": round(float(f1_score(y_va, pred_va, zero_division=0)), 3),
        "base_churn_rate": round(float(feats["churned"].mean()), 3),
        "n_train": int(len(y_tr)),
        "n_test": int(len(y_va)),
        "confusion_matrix": confusion_matrix(y_va, pred_va).tolist(),
        "threshold": 0.5,
        "calibrated": False,
        "calibration": {
            "applied": False,
            "method": None,
            "fit_on": None,
            "metrics_basis": "uncalibrated_model_threshold_0.5",
            "note": (
                "Single-cutoff random-split path (short history): probabilities "
                "are the model's raw estimates, not post-hoc calibrated."
            ),
        },
    }
    importances = _permutation_importances(
        model, X_va, y_va, cols, random_state=random_state
    )
    shap_importance = _shap_global_importance(model, X_va, cols, random_state=random_state)

    live = compute_customer_features(tx, snapshot, schema)
    live_proba = model.predict_proba(live[cols].to_numpy(dtype=float))[:, 1]
    live = live.copy()
    live["churn_probability"] = np.round(live_proba, 4)
    return {
        "metrics": metrics,
        "importances": importances,
        "shap_importance": shap_importance,
        "scored": live,
        "feature_cols": cols,
        "cutoff": cutoff,
        "training_cutoffs": [str(cutoff.date())],
        "_model": model,
    }


def _tree_shap_values(model, X: np.ndarray) -> np.ndarray:
    """Raw SHAP values via TreeExplainer for a fitted HistGradientBoostingClassifier.

    Always explains the *base* estimator, never a calibration wrapper: sigmoid
    calibration is a monotonic rescaling fit on top of the raw decision
    function, so it doesn't change which features drive a prediction — only
    SHAP's scale would be harder to interpret if computed through it.
    """
    import shap

    explainer = shap.TreeExplainer(model)
    try:
        raw = explainer.shap_values(X)
    except Exception:
        # HistGradientBoostingClassifier's histogram-binned splits occasionally
        # trip TreeExplainer's strict additivity check with a tiny floating-point
        # mismatch; the values themselves are still valid, so retry without it
        # rather than losing the explanation entirely.
        raw = explainer.shap_values(X, check_additivity=False)
    return np.asarray(raw[1] if isinstance(raw, list) else raw)


def _shap_global_importance(
    model, X_va: np.ndarray, cols: list[str],
    random_state: int = 42, max_rows: int = 2000,
) -> list[dict]:
    """Mean absolute SHAP value per feature on (a sample of) the validation
    window — a per-prediction attribution technique, complementary to (not a
    replacement for) the permutation importance above."""
    try:
        if len(X_va) > max_rows:
            rng = np.random.default_rng(random_state)
            idx = rng.choice(len(X_va), size=max_rows, replace=False)
            X_va = X_va[idx]
        values = _tree_shap_values(model, X_va)
        mean_abs = np.abs(values).mean(axis=0)
        pairs = sorted(zip(cols, mean_abs), key=lambda t: -t[1])
        return [{"feature": f, "importance": round(float(v), 4)} for f, v in pairs]
    except Exception:
        return []


def explain_predictions(
    model, X: np.ndarray, cols: list[str], row_ids: list, top_k: int = 3,
) -> dict[str, list[dict]]:
    """Per-row SHAP driver breakdown for a specific, already-selected set of
    rows (e.g. the at-risk customers about to be shown) — kept separate from
    the global importance above so callers only pay the SHAP cost for rows
    they'll actually display, not the whole customer base."""
    if model is None or len(row_ids) == 0:
        return {}
    try:
        values = _tree_shap_values(model, X)
        out: dict[str, list[dict]] = {}
        for i, rid in enumerate(row_ids):
            row_vals = values[i]
            order = np.argsort(-np.abs(row_vals))[:top_k]
            out[str(rid)] = [
                {
                    "feature": cols[j],
                    "shap_value": round(float(row_vals[j]), 4),
                    "direction": "increases_risk" if row_vals[j] > 0 else "decreases_risk",
                }
                for j in order
            ]
        return out
    except Exception:
        return {}


def _permutation_importances(
    model, X_va: np.ndarray, y_va: np.ndarray, cols: list[str],
    random_state: int = 42, max_rows: int = 2000,
) -> list[dict]:
    """Permutation importance on (a sample of) the validation window."""
    try:
        from sklearn.inspection import permutation_importance

        if len(np.unique(y_va)) < 2 or len(y_va) < MIN_VALID_ROWS:
            return []
        if len(y_va) > max_rows:
            rng = np.random.default_rng(random_state)
            idx = rng.choice(len(y_va), size=max_rows, replace=False)
            X_va, y_va = X_va[idx], y_va[idx]
        result = permutation_importance(
            model, X_va, y_va, scoring="roc_auc",
            n_repeats=5, random_state=random_state,
        )
        pairs = sorted(
            zip(cols, result.importances_mean), key=lambda t: -t[1]
        )
        return [
            {"feature": f, "importance": round(float(max(i, 0.0)), 4)}
            for f, i in pairs
        ]
    except Exception:
        return []


def heuristic_predict(
    tx: pd.DataFrame, snapshot: pd.Timestamp, horizon: int, schema: dict
) -> dict:
    """Fallback when a supervised model can't be trained.

    Probability rises with recency relative to the horizon: a customer whose
    last purchase is older than the horizon is treated as (almost) churned.
    """
    live = compute_customer_features(tx, snapshot, schema)
    recency = live["recency_days"].to_numpy(dtype=float)
    proba = np.clip(recency / max(horizon, 1), 0.0, 1.0)
    live = live.copy()
    live["churn_probability"] = np.round(proba, 4)
    metrics = {
        "name": "Recency heuristic (fallback)",
        "validation": "none",
        "auc": None,
        "note": "Too little history/variation to train a classifier; "
                "scored by recency relative to the churn horizon.",
        "base_churn_rate": round(float((proba >= 0.5).mean()), 3),
    }
    return {"metrics": metrics, "importances": [], "shap_importance": [], "scored": live,
            "feature_cols": feature_columns(live),
            "cutoff": snapshot - pd.Timedelta(days=horizon),
            "training_cutoffs": [], "_model": None}
