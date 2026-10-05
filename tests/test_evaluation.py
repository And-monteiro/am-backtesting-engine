import numpy as np
import pandas as pd
import pytest

from am_backtesting.evaluate import evaluate, retain
from am_backtesting.models import Rules
from am_backtesting.util import EngineError


def test_drawdown_includes_initial_cash():
    result = evaluate(
        pd.date_range("2000", periods=3, freq="h", tz="UTC"),
        np.array([800.0, 900.0, 1000.0]),
        Rules(),
        [],
    )
    assert result["metrics"]["max_drawdown"] == pytest.approx(0.2)
    assert not result["rules"]["drawdown"]["passed"]


def test_flat_cash_does_not_beat_two_percent():
    result = evaluate(
        pd.date_range("2000", periods=9000, freq="h", tz="UTC"), np.full(9000, 1000.0), Rules(), []
    )
    assert not result["qualified"]
    assert not result["rules"]["beat_cash"]["passed"]
    assert result["metrics"]["benchmark_return"] > 0.02


def test_chunks_account_for_prior_chunk_equity():
    result = evaluate(
        pd.date_range("2000", periods=6, freq="h", tz="UTC"),
        np.array([1100.0, 1100.0, 1000.0, 1000.0, 1200.0, 1200.0]),
        Rules(),
        [],
    )
    assert result["metrics"]["chunk_returns"] == pytest.approx([0.1, -1 / 11, 0.2])
    assert result["rules"]["consistency"]["passed"]


def make_result(name, annual, values):
    return {
        "run_id": name,
        "qualified": True,
        "experiment_id": "one",
        "dataset_id": "one",
        "period": "build",
        "start": "a",
        "end": "b",
        "metrics": {"annualized_return": annual, "max_drawdown": 0.1},
        "daily_returns": {str(i): x for i, x in enumerate(values)},
    }


def test_correlation_keeps_better_and_deterministic():
    values = [float(i % 2) / 100 for i in range(100)]
    a, b = make_result("a", 0.1, values), make_result("b", 0.2, values)
    assert retain([a, b], Rules())["kept"] == ["b"]
    assert retain([b, a], Rules())["kept"] == ["b"]


def test_constant_returns_not_comparable():
    result = retain(
        [make_result("a", 0.1, [0.0] * 100), make_result("b", 0.2, [0.0] * 100)], Rules()
    )
    assert result["comparisons"][0]["status"] == "not_comparable"


def test_mixed_period_retention_rejected():
    a, b = make_result("a", 0.1, [0.0] * 100), make_result("b", 0.2, [0.0] * 100)
    b["period"] = "test"
    with pytest.raises(EngineError):
        retain([a, b], Rules())
