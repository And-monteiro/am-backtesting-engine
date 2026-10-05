import math

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from am_backtesting.data import hourly_grid, validate_prices
from am_backtesting.engine import simulate
from am_backtesting.features import expression_series, feature_series
from am_backtesting.models import Expression, Feature, Instrument, Rules, Schedule, Strategy, Venue
from am_backtesting.proof import synthetic_frame
from am_backtesting.util import EngineError


def run(strategy, rules, frame, **kwargs):
    return simulate(
        strategy,
        rules,
        {"BTCUSDT": frame},
        frame.index[0],
        frame.index[-1] + pd.Timedelta(hours=1),
        **kwargs,
    )


def test_close_signal_fills_next_observed_open(strategy, rules, frame):
    frame.loc[frame.index[1], ["open", "high", "low", "close"]] = 200
    result = run(strategy, rules, frame)
    first = result["fills"][0]
    assert first["open_time"] == frame.index[1].isoformat()
    assert first["decision_time"] == frame.index[1].isoformat()
    assert first["price"] == 200 * 1.0005
    assert first["side"] == "buy"


def test_round_trip_costs_independent_accounting(strategy, rules, frame):
    result = run(strategy, rules, frame.iloc[:3])
    q = math.floor(100 / (100.05 * 1.0009) / 1e-8) * 1e-8
    expected = 1000 - q * 100.05 * 1.0009 + q * 99.95 * 0.9991
    assert result["ending_cash"] == pytest.approx(expected)
    assert len(result["completed_trades"]) == 1
    assert result["costs"]["fees"] == pytest.approx(q * (100.05 + 99.95) * 0.0009)
    assert result["costs"]["adverse_execution"] == pytest.approx(q * 0.1)


def test_cash_budget_never_negative(strategy, rules, frame):
    result = run(strategy.model_copy(update={"fraction": 1.0}), rules, frame)
    assert result["ending_cash"] >= -1e-8
    assert all(t["quantity"] > 0 for t in result["fills"])


def test_fixed_fraction_does_not_rebalance_each_hour(strategy, rules, frame):
    result = run(strategy.model_copy(update={"max_holding_hours": None}), rules, frame)
    assert len(result["fills"]) == 1


def test_three_hour_gap_delays_fill_and_preserves_window(strategy, rules, frame):
    gap = frame.drop(frame.index[1:4])
    result = run(strategy, rules, gap)
    assert result["fills"][0]["open_time"] == frame.index[4].isoformat()
    assert result["data_quality"]["BTCUSDT"]["synthetic_hours"] == 3
    assert len(result["equity"]) == 8


def test_gap_expiry_prevents_late_fill(strategy, rules, frame):
    gap = frame.drop(frame.index[1:6])
    result = run(strategy, rules, gap)
    assert result["data_quality"]["BTCUSDT"]["expired_orders"] == 1
    assert not any(fill["open_time"] == frame.index[6].isoformat() for fill in result["fills"])


def test_no_future_interpolation_and_no_exterior_fill(frame):
    frame.loc[frame.index[6] :, ["open", "high", "low", "close"]] = 1000
    grid = hourly_grid(
        frame.drop(frame.index[1:6]),
        pd.date_range(frame.index[0] - pd.Timedelta(hours=1), periods=11, freq="h"),
        terminal_open=frame.index[-1],
    )
    assert grid.close.iloc[1:6].tolist() == [100.0] * 5
    assert pd.isna(grid.close.iloc[6])  # fifth missing hour
    assert pd.isna(grid.close.iloc[0])
    assert pd.isna(grid.close.iloc[-1])
    assert pd.isna(grid.volume_base.iloc[2])


def test_gap_treatment_does_not_depend_on_a_future_recovery(frame):
    timeline = frame.index
    prefix = frame.iloc[:1]
    recovered = frame.drop(frame.index[1:5])
    without_future = hourly_grid(prefix, timeline)
    with_future = hourly_grid(recovered, timeline)
    pd.testing.assert_series_equal(without_future.close.iloc[:5], with_future.close.iloc[:5])


def test_unresolved_final_position_cannot_qualify(strategy, rules, frame):
    result = simulate(
        strategy.model_copy(update={"max_holding_hours": None}),
        rules,
        {"BTCUSDT": frame.iloc[:3]},
        frame.index[0],
        frame.index[-1] + pd.Timedelta(hours=1),
    )
    assert result["unresolved_stale_positions"] == ["BTCUSDT"]
    assert not result["rules"]["no_unresolved_stale_exposure"]["passed"]


def test_stale_held_position_is_disclosed(strategy, rules, frame):
    gap = frame.drop(frame.index[2:6])
    result = run(strategy.model_copy(update={"max_holding_hours": None}), rules, gap)
    assert result["data_quality"]["BTCUSDT"]["stale_position_hours"] == 4
    assert result["ending_positions"]


def test_terminal_liquidation_at_open_and_stops(strategy, rules, frame):
    result = run(
        strategy.model_copy(update={"max_holding_hours": None}),
        rules,
        frame,
        terminal={"BTCUSDT": frame.index[4]},
    )
    assert not result["ending_positions"]
    assert result["fills"][-1]["reason"] == "assumed_terminal_liquidation"
    assert result["fills"][-1]["open_time"] == frame.index[4].isoformat()
    assert len(result["fills"]) == 2


def test_reject_orders_below_minimum(strategy, rules, frame):
    result = run(strategy, rules, frame, instruments={"BTCUSDT": Instrument(min_notional=500)})
    assert not result["fills"]
    assert result["data_quality"]["BTCUSDT"]["rejected_orders"] > 0


def test_alpaca_fee_deducted_from_received_crypto(strategy, frame):
    rules = Rules(venue=Venue(name="alpaca", fee_bps=25, fee_asset="received"))
    result = run(strategy, rules, frame.iloc[:2])
    fill = result["fills"][0]
    assert fill["quantity"] == pytest.approx(fill["gross_quantity"] * 0.9975)
    assert result["ending_cash"] == pytest.approx(1000 - fill["gross_quantity"] * fill["price"])


def test_alpaca_dust_does_not_block_subsequent_trades(strategy, frame):
    rules = Rules(venue=Venue(name="alpaca", fee_bps=25, fee_asset="received"))
    result = run(strategy, rules, frame)
    assert len(result["completed_trades"]) == 3
    assert result["dust_positions"]
    assert len([f for f in result["fills"] if f["side"] == "buy"]) == 4


def test_no_cost_free_profile():
    with pytest.raises(ValidationError):
        Venue(fee_bps=0)
    with pytest.raises(ValidationError):
        Venue(fee_bps=1)
    with pytest.raises(ValidationError):
        Venue(adverse_execution_bps=0)


@pytest.mark.parametrize(
    "payload",
    [
        {"op": "lag", "periods": -1, "args": [{"op": "field", "name": "close"}]},
        {"op": "field", "name": "tomorrow_close"},
        {"op": "constant", "value": float("nan")},
        {"op": "gt", "args": []},
    ],
)
def test_bad_expressions_rejected(payload):
    with pytest.raises(ValidationError):
        Expression.model_validate(payload)


def test_signal_numeric_condition_rejected(strategy, rules, frame):
    invalid = strategy.model_copy(update={"entry": Expression(op="field", name="close")})
    with pytest.raises(EngineError, match="booleans"):
        run(invalid, rules, frame)


def test_unknown_comparison_never_becomes_true(frame):
    grid = hourly_grid(frame.drop(frame.index[1]), frame.index)
    expr = Expression(
        op="not",
        args=[
            Expression(
                op="gt",
                args=[
                    Expression(op="field", name="volume_base"),
                    Expression(op="constant", value=10),
                ],
            )
        ],
    )
    result = expression_series(expr, grid, {})
    assert pd.isna(result.iloc[1])


def test_daily_feature_available_only_at_day_end():
    values = np.repeat([100.0, 200.0, 300.0], 24)
    frame = synthetic_frame(values, values, np.ones(72))
    result = feature_series(
        hourly_grid(frame, frame.index), Feature(kind="sma", window=2, timeframe="1d")
    )
    assert pd.isna(result.iloc[46])
    assert result.iloc[47] == 150
    assert result.iloc[48] == 150
    assert result.iloc[71] == 250


def test_daily_feature_resets_after_long_gap():
    values = np.repeat(np.arange(1.0, 6.0), 24)
    frame = synthetic_frame(values, values, np.ones(120))
    grid = hourly_grid(frame.drop(frame.index[48:60]), frame.index)
    result = feature_series(grid, Feature(kind="sma", window=2, timeframe="1d"))
    assert pd.isna(result.iloc[71])
    assert pd.isna(result.iloc[95])
    assert result.iloc[119] == 4.5


def test_future_mutations_cannot_change_prior_features(frame):
    original = feature_series(hourly_grid(frame, frame.index), Feature(kind="sma", window=2))
    changed = frame.copy()
    changed.iloc[5:, :4] = 1000
    future = feature_series(hourly_grid(changed, frame.index), Feature(kind="sma", window=2))
    pd.testing.assert_series_equal(original.iloc[:5], future.iloc[:5])


def test_ranking_equal_weights_and_ties(rules, frame):
    s = Strategy(
        name="rank",
        assets=["ETHUSDT", "BTCUSDT"],
        mode="rank",
        allocation="equal_weight",
        rank_by=Expression(op="field", name="close"),
        top_n=1,
    )
    result = simulate(
        s,
        rules,
        {"BTCUSDT": frame, "ETHUSDT": frame.copy()},
        frame.index[0],
        frame.index[-1] + pd.Timedelta(hours=1),
    )
    assert result["fills"][0]["asset"] == "BTCUSDT"
    assert all(f["asset"] == "BTCUSDT" for f in result["fills"])
    assert result["ending_cash"] >= 0


def test_weekly_monday_open_uses_sunday_close(rules):
    values = np.full(200, 100.0)
    frame = synthetic_frame(values, values, np.ones(200))
    s = Strategy(
        name="rank",
        assets=["BTCUSDT"],
        mode="rank",
        allocation="equal_weight",
        rank_by=Expression(op="field", name="close"),
        schedule=Schedule(frequency="weekly"),
    )
    result = run(s, rules, frame)
    first = pd.Timestamp(result["fills"][0]["open_time"])
    assert first.weekday() == 0 and first.hour == 0


def test_crossovers_with_volatility_filter(rules):
    values = np.r_[np.linspace(110, 90, 60), np.linspace(90, 120, 80), np.linspace(120, 80, 80)]
    frame = synthetic_frame(values, values, np.ones(len(values)))
    fast, slow, vol = [Expression(op="feature", name=x) for x in ["fast", "slow", "vol"]]
    s = Strategy(
        name="crossover",
        assets=["BTCUSDT"],
        features={
            "fast": Feature(kind="sma", window=20),
            "slow": Feature(kind="sma", window=50),
            "vol": Feature(kind="volatility", window=20),
        },
        entry=Expression(
            op="all",
            args=[
                Expression(op="crosses_above", args=[fast, slow]),
                Expression(op="lt", args=[vol, Expression(op="constant", value=0.1)]),
            ],
        ),
        exit=Expression(op="crosses_below", args=[fast, slow]),
    )
    result = run(s, rules, frame)
    assert [f["side"] for f in result["fills"]] == ["buy", "sell"]


@pytest.mark.parametrize("mutation", ["duplicate", "negative", "ohlc", "null", "off_hour"])
def test_data_validation(frame, mutation):
    raw = frame.reset_index(names="open_time_utc")
    if mutation == "duplicate":
        raw.loc[1, "open_time_utc"] = raw.loc[0, "open_time_utc"]
    elif mutation == "negative":
        raw.loc[0, "close"] = -1
    elif mutation == "ohlc":
        raw.loc[0, "high"] = 50
    elif mutation == "null":
        raw.loc[0, "volume_base"] = np.nan
    else:
        raw.loc[0, "open_time_utc"] += pd.Timedelta(minutes=1)
    with pytest.raises(EngineError):
        validate_prices(raw)
