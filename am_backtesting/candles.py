"""In-memory candle validation and causal hourly grids; no filesystem access."""

import numpy as np
import pandas as pd

from .util import EngineError

FIELDS = ["open", "high", "low", "close", "volume_base", "volume_quote", "trades"]


def validate_prices(frame: pd.DataFrame) -> pd.DataFrame:
    try:
        index = pd.DatetimeIndex(pd.to_datetime(frame.pop("open_time_utc"), utc=True))
        frame = frame[FIELDS].apply(pd.to_numeric, errors="raise")
    except (KeyError, ValueError) as error:
        raise EngineError("BAD_DATA", str(error)) from error
    if (
        index.has_duplicates
        or not index.is_monotonic_increasing
        or not index.equals(index.floor("h"))
    ):
        raise EngineError("BAD_DATA", "Timestamps must be ascending unique hourly opens")
    values = frame.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise EngineError("BAD_DATA", "Nonfinite or nonpositive prices")
    if (frame[["volume_base", "volume_quote", "trades"]] < 0).any().any():
        raise EngineError("BAD_DATA", "Negative activity")
    if (
        (frame.high < frame[["open", "close", "low"]].max(axis=1))
        | (frame.low > frame[["open", "close", "high"]].min(axis=1))
    ).any():
        raise EngineError("BAD_DATA", "Invalid OHLC ordering")
    if (frame.trades != np.floor(frame.trades)).any():
        raise EngineError("BAD_DATA", "Trade counts must be integral")
    frame.index = index
    return frame.astype(float)


def hourly_grid(frame: pd.DataFrame, index: pd.DatetimeIndex, terminal_open=None):
    grid = frame.reindex(index).copy()
    observed = grid.close.notna()
    observed_positions = np.where(observed, np.arange(len(grid)), -1)
    last_position = np.maximum.accumulate(observed_positions)
    age = np.arange(len(grid)) - last_position
    # A later observed row must not decide whether earlier missing bars get filled.
    # Only an explicitly known cessation boundary can terminate synthetic continuity.
    within_history = (index >= frame.index.min()) if len(frame) else np.zeros(len(index), bool)
    if terminal_open is not None:
        within_history &= index <= terminal_open
    synthetic = ~observed & within_history & (last_position >= 0) & (age <= 4)
    previous = grid.close.ffill()
    for field in ("open", "high", "low", "close"):
        grid.loc[synthetic, field] = previous.loc[synthetic]
    grid["observed"] = observed
    grid["synthetic"] = synthetic
    grid["stale_age"] = np.where(last_position >= 0, age, -1)
    grid["valuation"] = previous
    return grid
