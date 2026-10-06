"""Fixed synthetic acceptance checks. Never reads the real price directory."""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import ValidationError

from .candles import FIELDS
from .engine import simulate
from .models import Expression, Rules, Strategy
from .util import atomic_json, engine_hash, file_hash, fingerprint


def synthetic_frame(opens, closes, volume):
    n = len(opens)
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "volume_base": volume,
            "volume_quote": volume * closes,
            "trades": np.ones(n),
        },
        index=pd.date_range("2000-01-01", periods=n, freq="h", tz="UTC"),
    )[FIELDS]


def signal_strategy(entry, exit=None, name="proof"):
    return Strategy(
        name=name,
        assets=["BTCUSDT"],
        entry=entry,
        exit=exit or Expression(op="constant", value=False),
        max_holding_hours=1,
    )


def run_proof(config: Path, output: Path | None = None):
    criteria = json.loads((config / "proof.json").read_text(encoding="utf-8"))
    rules = Rules.model_validate_json((config / "defaults.json").read_text(encoding="utf-8"))
    checks = []
    try:
        settings = criteria["known_edge"]
        n = settings["hours"]
        edge = settings["predictable_return"]
        opens = np.where(np.arange(n) % 2 == 1, 100.0, 100.0 * (1 + edge))
        opens[0] = 100
        closes = np.where(np.arange(n) % 2 == 1, 100.0 * (1 + edge), 100.0)
        volumes = np.where(np.arange(n) % 2 == 0, 1000.0, 10.0)
        volumes[-2:] = 10  # End flat; no uncompleted final trade.
        frame = synthetic_frame(opens, closes, volumes)
        entry = Expression(
            op="gt",
            args=[Expression(op="field", name="volume_base"), Expression(op="constant", value=500)],
        )
        strategy = signal_strategy(entry)
        result = simulate(
            strategy,
            rules,
            {"BTCUSDT": frame},
            frame.index[0],
            frame.index[-1] + pd.Timedelta(hours=1),
        )
        expected = rules.initial_cash
        fee, slip = rules.venue.fee_bps / 10000, rules.venue.adverse_execution_bps / 10000
        for _ in range((n - 2) // 2):
            quantity = math.floor((expected * 0.1 / (100 * (1 + slip) * (1 + fee))) / 1e-8) * 1e-8
            expected += quantity * (
                100 * (1 + edge) * (1 - slip) * (1 - fee) - 100 * (1 + slip) * (1 + fee)
            )
        difference = abs(expected - result["ending_cash"])
        valid_timing = all(
            pd.Timestamp(fill["open_time"]) >= pd.Timestamp(fill["decision_time"])
            for fill in result["fills"]
        )
        passed = (
            result["metrics"]["net_return"] >= settings["minimum_net_return"]
            and result["metrics"]["completed_trade_count"] >= settings["required_completed_trades"]
            and difference <= settings["accounting_absolute_tolerance"]
            and result["qualified"]
            and valid_timing
            and not result["ending_positions"]
        )
        checks.append(
            {
                "name": "known_edge",
                "passed": bool(passed),
                "net_return": result["metrics"]["net_return"],
                "completed_trades": result["metrics"]["completed_trade_count"],
                "accounting_error": difference,
                "next_open_timing": valid_timing,
            }
        )
    except Exception as error:
        checks.append({"name": "known_edge", "passed": False, "error": str(error)})
    if checks[-1]["passed"]:
        try:
            settings = criteria["noise"]
            results = []
            returns = []
            field = Expression(op="field", name="close")
            lag = Expression(op="lag", periods=1, args=[field])
            entries = [
                Expression(op="constant", value=True),
                Expression(op="gt", args=[field, lag]),
                Expression(op="lt", args=[field, lag]),
            ]
            for seed in settings["seeds"]:
                rng = np.random.default_rng(seed)
                changes = rng.uniform(
                    -settings["hourly_return_bound"],
                    settings["hourly_return_bound"],
                    settings["hours"],
                )
                closes = 100 * np.cumprod(1 + changes)
                opens = np.r_[100.0, closes[:-1]]
                frame = synthetic_frame(opens, closes, np.ones(len(opens)))
                for entry in entries:
                    result = simulate(
                        signal_strategy(entry),
                        rules,
                        {"BTCUSDT": frame},
                        frame.index[0],
                        frame.index[-1] + pd.Timedelta(hours=1),
                    )
                    results.append(result)
                    returns.append(result["metrics"]["net_return"])
            positive_fraction = float(np.mean(np.array(returns) > 0))
            mean_return = float(np.mean(returns))
            qualified = sum(r["qualified"] for r in results)
            trades = min(r["metrics"]["completed_trade_count"] for r in results)
            passed = (
                positive_fraction <= settings["positive_fraction_upper_bound"]
                and mean_return <= settings["mean_net_return_upper_bound"]
                and qualified <= settings["maximum_qualified_runs"]
                and trades >= settings["minimum_completed_trades_per_run"]
            )
            checks.append(
                {
                    "name": "pure_noise",
                    "passed": bool(passed),
                    "trials": len(results),
                    "mean_net_return": mean_return,
                    "positive_fraction": positive_fraction,
                    "qualified_runs": qualified,
                    "minimum_trades": trades,
                    "fixture_scope": settings["rationale"],
                }
            )
        except Exception as error:
            checks.append({"name": "pure_noise", "passed": False, "error": str(error)})
    else:
        checks.append({"name": "pure_noise", "passed": False, "status": "not_run_after_failure"})
    if all(c["passed"] for c in checks):
        rejected = {}
        for name, payload in {
            "negative_lag": {
                "op": "lag",
                "periods": -1,
                "args": [{"op": "field", "name": "close"}],
            },
            "future_field": {"op": "field", "name": "next_close"},
        }.items():
            try:
                Expression.model_validate(payload)
                rejected[name] = False
            except ValidationError:
                rejected[name] = True
        try:
            Strategy.model_validate({**strategy.model_dump(mode="json"), "execution": "same_open"})
            rejected["same_open_override"] = False
        except ValidationError:
            rejected["same_open_override"] = True
        # A close-based input cannot fill at its own bar's open: first fill is next row.
        tiny = synthetic_frame(
            np.array([100.0, 120.0, 130.0]), np.array([110.0, 125.0, 130.0]), np.ones(3)
        )
        close_signal = signal_strategy(
            Expression(
                op="gt",
                args=[Expression(op="field", name="close"), Expression(op="constant", value=105)],
            )
        )
        trial = simulate(
            close_signal,
            rules,
            {"BTCUSDT": tiny},
            tiny.index[0],
            tiny.index[-1] + pd.Timedelta(hours=1),
        )
        timing = bool(
            trial["fills"]
            and trial["fills"][0]["open_time"] == tiny.index[1].isoformat()
            and trial["fills"][0]["price"] == 120 * (1 + rules.venue.adverse_execution_bps / 10000)
        )
        checks.append(
            {
                "name": "cheating_signal",
                "passed": all(rejected.values()) and timing,
                "rejected": rejected,
                "legitimate_close_to_next_open": timing,
            }
        )
    else:
        checks.append(
            {"name": "cheating_signal", "passed": False, "status": "not_run_after_failure"}
        )
    report = {
        "passed": all(c["passed"] for c in checks),
        "engine_sha256": engine_hash(),
        "criteria_sha256": file_hash(config / "proof.json"),
        "defaults_sha256": file_hash(config / "defaults.json"),
        "rules_sha256": file_hash(config / "rules.md"),
        "criteria": criteria,
        "rules": rules.model_dump(mode="json"),
        "checks": checks,
        "real_strategies_run": False,
        "sealed_data_read": False,
    }
    report["report_id"] = fingerprint(report)
    if output is not None:
        atomic_json(output, report)
    return report
