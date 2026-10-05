"""Chronological spot execution. Features may be vectorized; orders and cash are stateful."""

import math

import numpy as np
import pandas as pd

from .data import hourly_grid
from .features import boolean_array, expression_series, feature_series
from .models import Instrument, Rules, Strategy
from .util import EngineError


def simulate(
    strategy: Strategy,
    rules: Rules,
    frames: dict,
    start,
    end,
    instruments: dict[str, Instrument] | None = None,
    terminal: dict | None = None,
):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start >= end:
        raise EngineError("NO_DATA", "Empty evaluation interval")
    assets = sorted(strategy.assets)
    if set(frames) != set(assets):
        raise EngineError("ASSET_UNAVAILABLE", "Frames must match strategy assets")
    available = [f.index.min() for f in frames.values() if len(f)]
    if not available or not any(
        ((f.index >= start) & (f.index < end)).any() for f in frames.values()
    ):
        raise EngineError("NO_DATA", "No observations in evaluation interval")
    if (end - min(min(available), start)) > pd.Timedelta(days=365 * 20):
        raise EngineError("RUN_TOO_LARGE", "Evaluation plus warmup exceeds 20 years")
    index = pd.date_range(min(min(available), start), end, freq="h", inclusive="left")
    begin = int(index.searchsorted(start))
    terminal = terminal or {}
    grids = [
        hourly_grid(frames[a].loc[frames[a].index < end], index, terminal.get(a)) for a in assets
    ]
    n, m = len(index), len(assets)
    entry, exits, rankings = [], [], []
    diagnostics = {}
    for asset, grid in zip(assets, grids):
        feats = {name: feature_series(grid, spec) for name, spec in strategy.features.items()}
        entry.append(boolean_array(expression_series(strategy.entry, grid, feats)))
        exits.append(
            boolean_array(expression_series(strategy.exit, grid, feats))
            if strategy.exit
            else np.zeros(n, bool)
        )
        rank = (
            expression_series(strategy.rank_by, grid, feats)
            if strategy.rank_by
            else pd.Series(0.0, index=index)
        )
        if pd.api.types.is_bool_dtype(rank.dtype):
            raise EngineError("EXPRESSION_TYPE", "Rank expression must be numeric")
        rankings.append(rank.to_numpy(dtype=float, na_value=np.nan))
        live = grid.iloc[begin:]
        missing = ~live.observed & (live.index >= frames[asset].index.min())
        if asset in terminal:
            missing &= live.index <= terminal[asset]
        diagnostics[asset] = {
            "missing_hours": int(missing.sum()),
            "synthetic_hours": int(live.synthetic.sum()),
            "gap_episodes": int((missing & ~missing.shift(1, fill_value=False)).sum()),
            "skipped_decisions": int((~live.observed).sum()),
            "expired_orders": 0,
            "rejected_orders": 0,
            "stale_position_hours": 0,
            "terminal_liquidation": None,
        }
    entry, exits, rankings = np.array(entry).T, np.array(exits).T, np.array(rankings).T
    opens = np.column_stack([g.open.to_numpy() for g in grids])
    observed = np.column_stack([g.observed.to_numpy() for g in grids])
    valuations = np.column_stack([g.valuation.to_numpy() for g in grids])
    terminal_indices = {
        assets.index(a): int(index.searchsorted(ts))
        for a, ts in terminal.items()
        if a in assets and start <= ts < end
    }
    qty = np.zeros(m)
    dust = np.zeros(m)
    entry_index = np.full(m, -1)
    cash = float(rules.initial_cash)
    fee_rate = rules.venue.fee_bps / 10000
    slip = rules.venue.adverse_execution_bps / 10000
    pending = {}
    fills, completed = [], []
    round_cost, round_proceeds, round_acquired = np.zeros(m), np.zeros(m), np.zeros(m)
    curve = np.empty(n - begin)
    total_fees = total_execution = 0.0
    stopped = set()

    def sell(j, amount, i, reason, decision_time=None, terminal_fill=False):
        nonlocal cash, total_fees, total_execution
        amount = min(amount, qty[j])
        if amount <= 1e-12:
            return
        asset = assets[j]
        spec = (instruments or {}).get(asset, Instrument())
        # A full quote-fee position was acquired in valid quantity increments already.
        # Re-flooring its floating representation can strand an entire tick as dust.
        full_quote_exit = amount == qty[j] and rules.venue.fee_asset == "quote"
        if not terminal_fill and not full_quote_exit:
            amount = (
                math.floor((amount + spec.quantity_step * 1e-9) / spec.quantity_step)
                * spec.quantity_step
            )
        price = opens[i, j] * (1 - slip)
        notional = amount * price
        if not terminal_fill and (
            amount < spec.min_quantity
            or amount > spec.max_quantity
            or notional < spec.min_notional
            or notional > spec.max_notional
        ):
            diagnostics[asset]["rejected_orders"] += 1
            return
        fee = notional * fee_rate
        cash += notional - fee
        qty[j] -= amount
        total_fees += fee
        total_execution += amount * opens[i, j] * slip
        round_proceeds[j] += notional - fee
        fills.append(
            {
                "asset": asset,
                "side": "sell",
                "open_time": index[i].isoformat(),
                "decision_time": decision_time,
                "quantity": amount,
                "price": price,
                "fee_quote_equivalent": fee,
                "reason": reason,
            }
        )
        if qty[j] < spec.quantity_step:
            residual_basis = (
                round_cost[j] * qty[j] / round_acquired[j] if round_acquired[j] else 0.0
            )
            dust[j] += qty[j]
            qty[j] = 0
            completed.append(
                {
                    "asset": asset,
                    "entry_open": index[entry_index[j]].isoformat(),
                    "exit_open": index[i].isoformat(),
                    "net_pnl": round_proceeds[j] - round_cost[j] + residual_basis,
                }
            )
            round_cost[j] = round_proceeds[j] = round_acquired[j] = 0
            entry_index[j] = -1

    def buy(j, budget, i, decision_time):
        nonlocal cash, total_fees, total_execution
        asset = assets[j]
        spec = (instruments or {}).get(asset, Instrument())
        price = opens[i, j] * (1 + slip)
        budget = min(budget, cash)
        received_fee = rules.venue.fee_asset == "received"
        gross = budget / price if received_fee else budget / (price * (1 + fee_rate))
        gross = math.floor(gross / spec.quantity_step) * spec.quantity_step
        notional = gross * price
        if (
            gross < spec.min_quantity
            or gross > spec.max_quantity
            or notional < spec.min_notional
            or notional > spec.max_notional
        ):
            diagnostics[asset]["rejected_orders"] += 1
            return
        fee = notional * fee_rate
        amount = gross * (1 - fee_rate) if received_fee else gross
        spent = notional if received_fee else notional + fee
        if qty[j] == 0:
            entry_index[j] = i
        qty[j] += amount
        round_acquired[j] += amount
        cash -= spent
        round_cost[j] += spent
        total_fees += fee
        total_execution += gross * opens[i, j] * slip
        fills.append(
            {
                "asset": asset,
                "side": "buy",
                "open_time": index[i].isoformat(),
                "decision_time": decision_time,
                "quantity": amount,
                "gross_quantity": gross,
                "price": price,
                "fee_quote_equivalent": fee,
                "reason": "strategy",
            }
        )

    for i in range(begin, n):
        # Phase 1: execute previous-close decisions at this observed open, sells before buys.
        for j, terminal_i in terminal_indices.items():
            if i == terminal_i:
                if not observed[i, j]:
                    raise EngineError(
                        "BAD_TERMINAL_EVENT", "Terminal liquidation requires an observed open"
                    )
                if dust[j] > 0:
                    # Terminal settlement explicitly includes fee-created fractional dust.
                    dust_notional = dust[j] * opens[i, j] * (1 - slip)
                    cash += dust_notional * (1 - fee_rate)
                    total_fees += dust_notional * fee_rate
                    total_execution += dust[j] * opens[i, j] * slip
                    fills.append(
                        {
                            "asset": assets[j],
                            "side": "sell",
                            "open_time": index[i].isoformat(),
                            "decision_time": None,
                            "quantity": float(dust[j]),
                            "price": opens[i, j] * (1 - slip),
                            "fee_quote_equivalent": dust_notional * fee_rate,
                            "reason": "assumed_terminal_dust_liquidation",
                        }
                    )
                    dust[j] = 0
                sell(j, qty[j], i, "assumed_terminal_liquidation", terminal_fill=True)
                diagnostics[assets[j]]["terminal_liquidation"] = index[i].isoformat()
                pending.pop(j, None)
                stopped.add(j)
        ready = []
        for j, order in list(pending.items()):
            if i > order["expires"]:
                diagnostics[assets[j]]["expired_orders"] += 1
                del pending[j]
            elif observed[i, j] and j not in stopped:
                ready.append((j, order))
                del pending[j]
        open_values = np.where(observed[i], opens[i], valuations[max(begin, i - 1)])
        equity_open = cash + float(np.sum((qty + dust) * np.nan_to_num(open_values)))
        for j, order in ready:
            if order["kind"] == "sell":
                sell(j, qty[j], i, "strategy", order["decision"])
            elif order["kind"] == "target":
                desired = equity_open * order["weight"]
                excess = qty[j] * opens[i, j] - desired
                if excess > 1e-8:
                    sell(j, excess / opens[i, j], i, "rebalance", order["decision"])
        for j, order in ready:
            if order["kind"] == "buy" and qty[j] == 0:
                buy(j, order["budget"], i, order["decision"])
            elif order["kind"] == "target":
                deficit = equity_open * order["weight"] - qty[j] * opens[i, j]
                if deficit > 1e-8:
                    buy(j, deficit, i, order["decision"])
        # Phase 2: mark equity at close. Carry stale valuations but disclose exposure.
        marks = valuations[i]
        equity_close = cash + float(np.sum((qty + dust) * np.nan_to_num(marks)))
        curve[i - begin] = equity_close
        if cash < -1e-7 or (qty < -1e-12).any():
            raise EngineError("ACCOUNTING", "Cash/position invariant failed")
        for j in range(m):
            if qty[j] + dust[j] > 0 and not observed[i, j]:
                diagnostics[assets[j]]["stale_position_hours"] += 1
        # Phase 3: decisions are timestamped at candle END; open and close phases never mix.
        decision = index[i] + pd.Timedelta(hours=1)
        schedule = strategy.schedule
        scheduled = schedule.frequency == "hourly" or (
            decision.hour == schedule.hour_utc
            and (schedule.frequency == "daily" or decision.weekday() == schedule.weekday)
        )
        if not scheduled:
            continue
        candidates = [j for j in range(m) if observed[i, j] and j not in stopped]
        expiry = i + strategy.order_expiry_hours
        if strategy.mode == "rank":
            eligible = [j for j in candidates if entry[i, j] and np.isfinite(rankings[i, j])]
            eligible.sort(
                key=lambda j: ((-1 if strategy.descending else 1) * rankings[i, j], assets[j])
            )
            selected = set(eligible[: strategy.top_n])
            frozen_value = sum(
                (qty[j] + dust[j]) * np.nan_to_num(marks[j])
                for j in range(m)
                if j not in candidates
            )
            allocatable = (
                max(0.0, strategy.max_exposure - frozen_value / equity_close)
                if equity_close > 0
                else 0
            )
            weight = allocatable / len(selected) if selected else 0
            for j in candidates:
                pending[j] = {
                    "kind": "target",
                    "weight": weight if j in selected else 0,
                    "expires": expiry,
                    "decision": decision.isoformat(),
                }
        else:
            for j in candidates:
                held_too_long = (
                    strategy.max_holding_hours is not None
                    and entry_index[j] >= 0
                    and i - entry_index[j] + 1 >= strategy.max_holding_hours
                )
                if qty[j] > 0 and (exits[i, j] or held_too_long):
                    pending[j] = {
                        "kind": "sell",
                        "expires": expiry,
                        "decision": decision.isoformat(),
                    }
                elif qty[j] == 0 and entry[i, j] and j not in pending:
                    pending[j] = {
                        "kind": "buy",
                        "budget": equity_close * strategy.fraction,
                        "expires": expiry,
                        "decision": decision.isoformat(),
                    }
    from .evaluate import evaluate

    evaluation = evaluate(index[begin:], curve, rules, completed)
    unresolved = [a for j, a in enumerate(assets) if qty[j] + dust[j] > 0 and not observed[-1, j]]
    evaluation["rules"]["no_unresolved_stale_exposure"] = {
        "passed": not unresolved,
        "actual": unresolved,
        "required": [],
    }
    evaluation["qualified"] &= not unresolved
    return {
        "status": "completed",
        "metrics": evaluation["metrics"],
        "rules": evaluation["rules"],
        "qualified": evaluation["qualified"],
        "daily_returns": evaluation["daily_returns"],
        "equity": [
            {"time": (ts + pd.Timedelta(hours=1)).isoformat(), "value": float(value)}
            for ts, value in zip(index[begin:], curve)
        ],
        "fills": fills,
        "completed_trades": completed,
        "ending_cash": cash,
        "ending_positions": {
            a: float(qty[j] + dust[j]) for j, a in enumerate(assets) if qty[j] + dust[j] > 0
        },
        "dust_positions": {a: float(dust[j]) for j, a in enumerate(assets) if dust[j] > 0},
        "costs": {"fees": total_fees, "adverse_execution": total_execution, "financing": 0.0},
        "data_quality": diagnostics,
        "unresolved_stale_positions": unresolved,
        "pending_orders_at_end": len(pending),
    }
