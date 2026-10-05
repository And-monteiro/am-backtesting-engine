import numpy as np
import pytest

from am_backtesting.models import Expression, Rules, Strategy
from am_backtesting.proof import synthetic_frame


@pytest.fixture
def rules():
    return Rules()


@pytest.fixture
def frame():
    return synthetic_frame(np.full(8, 100.0), np.full(8, 100.0), np.ones(8))


@pytest.fixture
def strategy():
    return Strategy(
        name="test",
        assets=["BTCUSDT"],
        entry=Expression(op="constant", value=True),
        exit=Expression(op="constant", value=False),
        max_holding_hours=1,
    )
