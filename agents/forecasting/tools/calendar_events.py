"""Moving-holiday regressors for the Islamic (Hijri) calendar.

STATUS: NOT WIRED IN. Kept as a validated experiment, not live code.
------------------------------------------------------------------
Wiring these columns into ``harmonic_regression`` was tried and measured
against a six-origin out-of-sample holdout, and reverted. The regressors did
what they were built to do — error on Ramadan/Eid windows fell from 28.8% to
19.9% MAPE — but overall accuracy got *worse*, from 13.9% to 15.2%, and 80%
interval coverage fell from 4/6 to 2/6.

The cause is model selection, not the regressors. Adding an exogenous input to
one candidate and not the others breaks the comparison the ``Auto`` selector
depends on: the back-test folds contain Ramadan, so the enriched model wins
every fold and is then chosen for ordinary windows too, where it is beaten by
the Ensemble and Holt-Winters. Harmonic Regression went from winning 3 of 6
origins to winning all 6, and ordinary-window MAPE doubled.

A correct implementation has to keep the selector's comparison fair. The
cheapest way is to register the enriched variant as its *own* candidate
("Harmonic Regression (Hijri)") so the back-test chooses between it and the
plain model on identical folds, and the current behaviour remains the floor
rather than being replaced. Until that exists, this module stays unused.

Why it exists at all
--------------------
Fourier seasonality is periodic in the *Gregorian* year. Ramadan is not: the
Hijri year is ~354 days, so Ramadan drifts about 11 days earlier every
Gregorian year and never repeats at the same annual phase. No number of
365.25-day harmonics can represent a cycle that moves — the best a harmonic
model can do is smear the effect across the whole spring and get every
individual year wrong.

For a business in Egypt or the wider MENA region this is not a minor gap. It is
the single most commercially important stretch of the year, and out-of-sample
validation on this project's own data showed the cost precisely: 6.5% MAPE on
ordinary 30-day windows against 28.8% on windows containing Ramadan or Eid —
under-forecasting the Ramadan build-up by 26%, then over-forecasting the
post-Eid month by 31% after mistaking the surge for a new level.

Each effect is modelled separately because they move in different
directions, and collapsing them into one "holiday" flag cancels them out:

* ``pre_ramadan``  — the stock-up week before the fast begins.
* ``ramadan``      — the level shift that holds across the whole month.
* ``ramadan_ramp`` — the build toward Eid, on top of that level. Level and ramp
                     are separate columns because a holiday has both, and a
                     model given only one of them fits the average of the other
                     and is wrong at both ends of the month.
* ``eid_fitr``     — the sharp multi-day peak closing Ramadan.
* ``eid_adha``     — the second Eid, ~70 days later, and a *separate* column.
                     The two are not interchangeable: they differ in
                     commercial magnitude by category and region, and giving
                     them one shared coefficient makes the model apply the
                     larger holiday's lift to the smaller one. That single
                     conflation cost ~1.7pp of error on an ordinary window in
                     this project's own out-of-sample test.
* ``post_eid``     — the slump immediately after, which is the half a naive
                     "holiday bump" regressor gets most wrong.

Dates
-----
Ramadan start dates are tabulated rather than computed. Hijri-to-Gregorian
conversion depends on lunar visibility and is announced per country, so a
table of observed/announced dates is *more* accurate than an arithmetic
conversion, and it avoids taking on a dependency for sixteen rows of data.
Dates outside the table simply produce zero columns, which the ridge fit
absorbs harmlessly — the model degrades to its previous behaviour rather than
failing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# First day of Ramadan, Gregorian. Sources: Umm al-Qura / regionally announced
# dates. Individual countries may differ by a day; that is well inside the
# resolution of a daily demand model.
RAMADAN_START = {
    2019: "2019-05-06", 2020: "2020-04-24", 2021: "2021-04-13",
    2022: "2022-04-02", 2023: "2023-03-23", 2024: "2024-03-11",
    2025: "2025-03-01", 2026: "2026-02-18", 2027: "2027-02-08",
    2028: "2028-01-28", 2029: "2029-01-16", 2030: "2030-01-05",
    2031: "2031-12-15", 2032: "2032-12-04", 2033: "2033-11-23",
    2034: "2034-11-12", 2035: "2035-11-01",
}
RAMADAN_DAYS = 29        # 29-30; the Eid window below absorbs the ambiguity
EID_FITR_DAYS = 4        # Eid al-Fitr, typically 3-4 days of holiday
PRE_RAMADAN_DAYS = 7     # the stock-up run before the fast
POST_EID_DAYS = 10       # the slump that follows
# Eid al-Adha falls ~70 days after Eid al-Fitr (10 Dhu al-Hijjah).
EID_ADHA_OFFSET = 70
EID_ADHA_DAYS = 4

FEATURE_NAMES = ("pre_ramadan", "ramadan", "ramadan_ramp", "eid_fitr", "eid_adha",
                 "post_eid")


def _windows(years: set[int]) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """Every modelled event window overlapping the given Gregorian years."""
    out: list[tuple[pd.Timestamp, pd.Timestamp, str]] = []
    # Adjacent years are included because a window can straddle 1 January.
    for year in sorted({y for base in years for y in (base - 1, base, base + 1)}):
        iso = RAMADAN_START.get(year)
        if iso is None:
            continue
        start = pd.Timestamp(iso)
        ram_end = start + pd.Timedelta(days=RAMADAN_DAYS - 1)
        fitr_start = ram_end + pd.Timedelta(days=1)
        fitr_end = fitr_start + pd.Timedelta(days=EID_FITR_DAYS - 1)
        adha_start = fitr_start + pd.Timedelta(days=EID_ADHA_OFFSET)
        out += [
            (start - pd.Timedelta(days=PRE_RAMADAN_DAYS), start - pd.Timedelta(days=1),
             "pre_ramadan"),
            (start, ram_end, "ramadan"),
            (start, ram_end, "ramadan_ramp"),
            (fitr_start, fitr_end, "eid_fitr"),
            (adha_start, adha_start + pd.Timedelta(days=EID_ADHA_DAYS - 1), "eid_adha"),
            (fitr_end + pd.Timedelta(days=1),
             fitr_end + pd.Timedelta(days=POST_EID_DAYS), "post_eid"),
        ]
    return out


def event_design(dates) -> np.ndarray:
    """``(len(dates), len(FEATURE_NAMES))`` matrix of Hijri event regressors.

    ``ramadan_ramp`` rises linearly from 0 to 1 across the month instead of
    being a flat indicator: demand builds toward Eid rather than stepping up on
    day one, and a flat dummy fits the average of a ramp — wrong at both ends.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
    out = np.zeros((len(idx), len(FEATURE_NAMES)), dtype=float)
    if len(idx) == 0:
        return out

    col = {name: i for i, name in enumerate(FEATURE_NAMES)}
    for lo, hi, name in _windows(set(idx.year.unique().tolist())):
        mask = (idx >= lo) & (idx <= hi)
        if not mask.any():
            continue
        j = col[name]
        if name == "ramadan_ramp":
            span = max((hi - lo).days, 1)
            out[mask, j] = np.clip((idx[mask] - lo).days / span, 0.0, 1.0)
        else:
            out[mask, j] = 1.0
    return out


def extend_dates(train_dates, h: int):
    """The next ``h`` timestamps after ``train_dates``, at its own cadence.

    A regressor is only useful if it can be evaluated for the periods being
    forecast, and models here are handed the training dates and a step count
    rather than an explicit future index.
    """
    idx = pd.DatetimeIndex(pd.to_datetime(train_dates))
    if len(idx) == 0 or h <= 0:
        return pd.DatetimeIndex([])
    if len(idx) == 1:
        step = pd.Timedelta(days=1)
    else:
        deltas = np.diff(idx.values).astype("timedelta64[s]").astype(float)
        step = pd.Timedelta(seconds=float(np.median(deltas)))
    if step <= pd.Timedelta(0):
        step = pd.Timedelta(days=1)
    return pd.DatetimeIndex([idx[-1] + step * (i + 1) for i in range(h)])
