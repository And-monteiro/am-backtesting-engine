"""AM Backtesting Engine: typed, transport-independent backtesting application API."""

from typing import TYPE_CHECKING

__version__ = "0.1.0"
__all__ = ["BacktestService"]

if TYPE_CHECKING:
    from .application import BacktestService


def __getattr__(name):
    if name == "BacktestService":
        from .application import BacktestService

        return BacktestService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
