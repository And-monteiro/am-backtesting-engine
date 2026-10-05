import numpy as np
import pandas as pd

from .models import Expression, Feature
from .util import EngineError


def feature_series(grid: pd.DataFrame, spec: Feature) -> pd.Series:
    values = grid[spec.field]
    if spec.timeframe == "1d":
        # Daily features publish at 23:00's close (00:00 next day), never at day's open.
        grouping = values.resample("1D")
        counts = grouping.count()
        if spec.field == "open":
            values = grouping.first()
        elif spec.field == "high":
            values = grouping.max()
        elif spec.field == "low":
            values = grouping.min()
        elif spec.field == "close":
            values = grouping.last()
        else:
            values = grouping.sum(min_count=24)
        values = values.where(counts == 24)
    window = spec.window
    if spec.kind == "sma":
        result = values.rolling(window, min_periods=window).mean()
    elif spec.kind == "ema":
        # Reset after unknown data, so a long gap cannot be silently bridged.
        groups = values.isna().cumsum()
        result = values.groupby(groups).transform(
            lambda x: x.ewm(span=window, adjust=False, min_periods=window).mean()
        )
        result = result.where(values.notna())
    elif spec.kind == "return":
        result = values / values.shift(window) - 1
        result = result.where(values.rolling(window + 1).count() == window + 1)
    elif spec.kind == "volatility":
        returns = values / values.shift(1) - 1
        result = returns.rolling(window, min_periods=window).std(ddof=1)
    elif spec.kind == "rolling_min":
        result = values.rolling(window, min_periods=window).min()
    else:
        result = values.rolling(window, min_periods=window).max()
    if spec.timeframe == "1d":
        result.index = result.index + pd.Timedelta(hours=23)
        # Preserve published NaN values; forward-fill only between publication times.
        result = result.reindex(grid.index, method="ffill")
    return result.replace([np.inf, -np.inf], np.nan)


def expression_series(
    expr: Expression, grid: pd.DataFrame, features: dict[str, pd.Series]
) -> pd.Series:
    op = expr.op
    if op == "constant":
        return pd.Series(expr.value, index=grid.index)
    if op == "field":
        return grid[expr.name]
    if op == "feature":
        return features[expr.name]
    args = [expression_series(x, grid, features) for x in expr.args]
    if op == "lag":
        return args[0].shift(expr.periods)
    if op in {"all", "any", "not"}:
        if any(not pd.api.types.is_bool_dtype(a.dtype) for a in args):
            raise EngineError("EXPRESSION_TYPE", "Logical operators require boolean expressions")
        result = args[0].astype("boolean")
        if op == "not":
            return ~result
        for arg in args[1:]:
            result = (
                result & arg.astype("boolean") if op == "all" else result | arg.astype("boolean")
            )
        return result
    left, right = args
    valid = left.notna() & right.notna()
    if op in {"crosses_above", "crosses_below"}:
        valid &= left.shift(1).notna() & right.shift(1).notna()
        result = (
            ((left > right) & (left.shift(1) <= right.shift(1)))
            if op == "crosses_above"
            else ((left < right) & (left.shift(1) >= right.shift(1)))
        )
        return result.astype("boolean").where(valid)
    functions = {
        "add": lambda: left + right,
        "subtract": lambda: left - right,
        "multiply": lambda: left * right,
        "divide": lambda: left / right.replace(0, np.nan),
        "gt": lambda: left > right,
        "gte": lambda: left >= right,
        "lt": lambda: left < right,
        "lte": lambda: left <= right,
        "eq": lambda: left == right,
    }
    result = functions[op]()
    if op in {"gt", "gte", "lt", "lte", "eq"}:
        return result.astype("boolean").where(valid)
    return result.replace([np.inf, -np.inf], np.nan)


def boolean_array(series: pd.Series) -> np.ndarray:
    if not pd.api.types.is_bool_dtype(series.dtype):
        raise EngineError("EXPRESSION_TYPE", "Entry and exit must produce booleans")
    return series.fillna(False).to_numpy(dtype=bool)
