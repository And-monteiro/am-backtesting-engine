"""Closed, versioned contracts. No arbitrary expressions or executable strategy code."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Expression(Contract):
    op: Literal[
        "constant",
        "field",
        "feature",
        "lag",
        "add",
        "subtract",
        "multiply",
        "divide",
        "gt",
        "gte",
        "lt",
        "lte",
        "eq",
        "all",
        "any",
        "not",
        "crosses_above",
        "crosses_below",
    ]
    value: float | bool | None = None
    name: str | None = None
    periods: int | None = Field(default=None, ge=1, le=10000)
    args: list["Expression"] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def shape(self):
        if self.op == "constant":
            valid = self.value is not None and not self.args and self.name is None
        elif self.op in {"field", "feature"}:
            valid = bool(self.name) and not self.args and self.value is None
            if self.op == "field" and self.name not in {
                "open",
                "high",
                "low",
                "close",
                "volume_base",
                "volume_quote",
                "trades",
            }:
                raise ValueError("unsupported market field")
        else:
            arity = 1 if self.op in {"not", "lag"} else 2
            valid = len(self.args) >= 1 if self.op in {"all", "any"} else len(self.args) == arity
            valid &= self.name is None and self.value is None
        if not valid or (self.op == "lag") != (self.periods is not None):
            raise ValueError("invalid expression shape; lags must be positive (no future access)")
        return self


class Feature(Contract):
    kind: Literal["sma", "ema", "return", "volatility", "rolling_min", "rolling_max"]
    field: Literal["open", "high", "low", "close", "volume_base", "volume_quote", "trades"] = (
        "close"
    )
    window: int = Field(ge=2, le=10000)
    timeframe: Literal["1h", "1d"] = "1h"


class Schedule(Contract):
    frequency: Literal["hourly", "daily", "weekly"] = "hourly"
    hour_utc: int = Field(default=0, ge=0, le=23)
    weekday: int = Field(default=0, ge=0, le=6, description="Monday=0; weekly only")


class Strategy(Contract):
    schema_version: Literal["1.0"] = "1.0"
    name: str = Field(min_length=1, max_length=100)
    assets: list[str] = Field(min_length=1, max_length=15)
    features: dict[str, Feature] = Field(default_factory=dict, max_length=32)
    mode: Literal["signals", "rank"] = "signals"
    entry: Expression = Field(default_factory=lambda: Expression(op="constant", value=True))
    exit: Expression | None = None
    rank_by: Expression | None = None
    top_n: int = Field(default=5, ge=1, le=15)
    descending: bool = True
    allocation: Literal["fixed_fraction", "equal_weight"] = "fixed_fraction"
    fraction: float = Field(default=0.1, gt=0, le=1)
    max_exposure: float = Field(default=1, gt=0, le=1)
    schedule: Schedule = Field(default_factory=Schedule)
    order_expiry_hours: int = Field(default=4, ge=1, le=168)
    max_holding_hours: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_strategy(self):
        if len(set(self.assets)) != len(self.assets):
            raise ValueError("duplicate assets")
        if any(not a.isalnum() or not a.endswith("USDT") or a == "HYPEUSDT" for a in self.assets):
            raise ValueError("assets must be allowed USDT symbols; HYPE is excluded")
        if self.mode == "rank" and (self.rank_by is None or self.allocation != "equal_weight"):
            raise ValueError("ranking requires rank_by and equal_weight allocation")
        if self.mode == "signals" and (self.exit is None or self.allocation != "fixed_fraction"):
            raise ValueError("signals require exit and fixed_fraction allocation")
        if self.mode == "signals" and self.fraction * len(self.assets) > self.max_exposure + 1e-12:
            raise ValueError("fixed allocations exceed max_exposure; select smaller fractions")
        nodes = 0

        def walk(expr, depth=0):
            nonlocal nodes
            nodes += 1
            if nodes > 256 or depth > 12:
                raise ValueError("expression complexity limit exceeded")
            if expr.op == "feature" and expr.name not in self.features:
                raise ValueError(f"unknown feature: {expr.name}")
            for arg in expr.args:
                walk(arg, depth + 1)

        for expr in (self.entry, self.exit, self.rank_by):
            if expr is not None:
                walk(expr)
        return self


class Venue(Contract):
    name: Literal["revolut_x_pt", "alpaca"] = "revolut_x_pt"
    fee_bps: float = Field(default=9, gt=0, le=1000)
    adverse_execution_bps: float = Field(default=5, gt=0, le=1000)
    fee_asset: Literal["quote", "received"] = "quote"

    @model_validator(mode="after")
    def profile(self):
        expected = (9, "quote") if self.name == "revolut_x_pt" else (25, "received")
        if (self.fee_bps, self.fee_asset) != expected:
            raise ValueError("profile requires Revolut 9bps/quote or Alpaca 25bps/received fees")
        if self.adverse_execution_bps < 5:
            raise ValueError("execution allowance must be at least the agreed 5bps per side")
        return self


class Rules(Contract):
    schema_version: Literal["1.0"] = "1.0"
    initial_cash: float = Field(default=1000, gt=0)
    currency: Literal["USDT"] = "USDT"
    min_trades: int = Field(default=300, ge=1)
    benchmark_annual_return: float = Field(default=0.02, ge=0, le=1)
    max_drawdown: float = Field(default=0.20, gt=0, le=1)
    min_positive_chunks: Literal[2] = 2
    correlation_limit: Literal[0.7] = 0.7
    correlation_min_days: int = Field(default=90, ge=3)
    small_gap_hours: Literal[4] = 4
    idle_cash_annual_return: Literal[0.0] = 0.0
    venue: Venue = Field(default_factory=Venue)


class ExperimentCreate(Contract):
    experiment_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    track: Literal["modern", "xmr"] = "modern"
    rules: Rules = Field(default_factory=Rules)


class RunRequest(Contract):
    experiment_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    candidate_id: str = Field(min_length=1, max_length=100)
    strategy: Strategy
    period: Literal["build", "test"] = "build"
    start: datetime | None = None
    end: datetime | None = None

    @model_validator(mode="after")
    def dates(self):
        for date in (self.start, self.end):
            if date is not None and (
                date.utcoffset() is None
                or date.astimezone(UTC).minute
                or date.astimezone(UTC).second
                or date.astimezone(UTC).microsecond
            ):
                raise ValueError("dates require a timezone and exact hourly boundaries")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise ValueError("start must precede end (exclusive)")
        return self


class Instrument(Contract):
    quantity_step: float = Field(default=0.00000001, gt=0)
    min_quantity: float = Field(default=0.00000001, gt=0)
    min_notional: float = Field(default=1, ge=0)
    max_notional: float = Field(default=200000, gt=0)
    max_quantity: float = Field(default=1e12, gt=0)


class ErrorBody(Contract):
    code: str
    message: str


class CorrelationRequest(Contract):
    run_ids: list[str] = Field(min_length=2, max_length=100)


class Fill(Contract):
    asset: str
    side: Literal["buy", "sell"]
    open_time: datetime
    decision_time: datetime | None
    quantity: float
    gross_quantity: float | None = None
    price: float
    fee_quote_equivalent: float
    reason: str


class CompletedTrade(Contract):
    asset: str
    entry_open: datetime
    exit_open: datetime
    net_pnl: float


class EquityPoint(Contract):
    time: datetime
    value: float


class RuleOutcome(Contract):
    passed: bool
    actual: float | int | list[float | str | None]
    required: float | int | list[str]


class Metrics(Contract):
    net_return: float
    annualized_return: float | None
    annualized_return_overflow: bool
    max_drawdown: float
    benchmark_return: float
    completed_trade_count: int
    chunk_returns: list[float | None]
    realized_pnl: float
    monthly_returns: dict[str, float]
    losing_months: int


class DataQuality(Contract):
    missing_hours: int
    synthetic_hours: int
    gap_episodes: int
    skipped_decisions: int
    expired_orders: int
    rejected_orders: int
    stale_position_hours: int
    terminal_liquidation: datetime | None


class Costs(Contract):
    fees: float
    adverse_execution: float
    financing: Literal[0.0]


class ExperimentResponse(ExperimentCreate):
    dataset_id: str
    policy_id: str


class BacktestResult(Contract):
    schema_version: Literal["1.0"]
    status: Literal["completed"]
    run_id: str
    experiment_id: str
    candidate_id: str
    policy_id: str
    dataset_id: str
    strategy_id: str
    engine_version: str
    engine_sha256: str
    period: Literal["build", "test"]
    track: Literal["modern", "xmr"]
    start: datetime
    end: datetime
    sealed_data_used: bool
    unseen_data_spent: bool
    price_source: Literal["Binance USDT"]
    venue_profile: Literal["revolut_x_pt", "alpaca"]
    constraints: dict[str, str]
    metrics: Metrics
    rules: dict[str, RuleOutcome]
    qualified: bool
    daily_returns: dict[str, float]
    equity: list[EquityPoint]
    fills: list[Fill]
    completed_trades: list[CompletedTrade]
    ending_cash: float
    ending_positions: dict[str, float]
    dust_positions: dict[str, float]
    costs: Costs
    data_quality: dict[str, DataQuality]
    unresolved_stale_positions: list[str]
    pending_orders_at_end: int


class Comparison(Contract):
    a: str
    b: str
    days: int
    correlation: float | None
    status: Literal["compared", "not_comparable"]


class Duplicate(Contract):
    run_id: str
    duplicate_of: str


class RetentionResult(Contract):
    kept: list[str]
    duplicates: list[Duplicate]
    comparisons: list[Comparison]
    failed_rules: list[str]
