"""Sanitizes agent/engine output (numpy scalars, pandas Timestamps, etc.) into
plain JSON-serializable Python before it goes into a FastAPI response body.
The cleaning/integrity/reconciliation modules return numpy/pandas-flavored
values internally (that's normal for pandas code) — this is the one seam
where it gets converted for the wire, so every router doesn't have to.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import pandas as pd


def _default(o: Any) -> Any:
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, pd.Period, pd.Timedelta)):
        return str(o)
    if hasattr(o, "item"):
        return o.item()
    return str(o)


def json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=_default))
