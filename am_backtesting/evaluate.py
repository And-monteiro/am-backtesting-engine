import math

import numpy as np
import pandas as pd

from .models import Rules
from .util import EngineError

YEAR_HOURS = 365.25 * 24


def evaluate(index, equity, rules: Rules, completed):
    initial = rules.initial_cash
    wealth = np.r_[initial, equity]
    drawdown = float(np.max(1 - wealth / np.maximum.accumulate(wealth)))
    total_return = float(equity[-1] / initial - 1)
    log_annual = (
        math.log(equity[-1] / initial) * YEAR_HOURS / len(equity) if equity[-1] > 0 else -math.inf
    )
    # Very short synthetic histories can have enormous annualized returns; JSON must be finite.
    annualized = math.expm1(log_annual) if log_annual < 700 else None
    benchmark = math.expm1(math.log1p(rules.benchmark_annual_return) * len(equity) / YEAR_HOURS)
    chunks, prior = [], initial
    for part in np.array_split(equity, 3):
        if len(part):
            chunks.append(float(part[-1] / prior - 1))
            prior = float(part[-1])
        else:
            chunks.append(None)
    outcomes = {
        "minimum_trades": {
            "passed": len(completed) >= rules.min_trades,
            "actual": len(completed),
            "required": rules.min_trades,
        },
        "beat_cash": {
            "passed": total_return > benchmark,
            "actual": total_return,
            "required": benchmark,
        },
        "drawdown": {
            "passed": drawdown < rules.max_drawdown
            and not math.isclose(drawdown, rules.max_drawdown, abs_tol=1e-12, rel_tol=0),
            "actual": drawdown,
            "required": rules.max_drawdown,
        },
        "consistency": {
            "passed": sum(x is not None and x > 0 for x in chunks) >= 2,
            "actual": chunks,
            "required": 2,
        },
    }
    # Sample equity at UTC day end, including idle days; first return starts from initial cash.
    series = pd.Series(equity, index=index)
    daily = series.resample("1D").last()
    returns = daily / daily.shift(1, fill_value=initial) - 1
    pnl = [t["net_pnl"] for t in completed]
    monthly = series.resample("MS").last()
    monthly_returns = monthly / monthly.shift(1, fill_value=initial) - 1
    return {
        "qualified": all(x["passed"] for x in outcomes.values()),
        "rules": outcomes,
        "metrics": {
            "net_return": total_return,
            "annualized_return": annualized,
            "annualized_return_overflow": annualized is None,
            "max_drawdown": drawdown,
            "benchmark_return": benchmark,
            "completed_trade_count": len(completed),
            "chunk_returns": chunks,
            "realized_pnl": sum(pnl),
            "monthly_returns": {t.isoformat(): float(v) for t, v in monthly_returns.items()},
            "losing_months": int((monthly_returns < 0).sum()),
        },
        "daily_returns": {t.isoformat(): float(v) for t, v in returns.items()},
    }


def retain(results: list[dict], rules: Rules):
    """Deterministic greedy selection; every retained pair is at/below the threshold."""
    if len({r["run_id"] for r in results}) != len(results):
        raise EngineError("DUPLICATE_RUN", "Run IDs must be unique")
    if (
        len(
            {
                (r["experiment_id"], r["dataset_id"], r["period"], r["start"], r["end"])
                for r in results
            }
        )
        != 1
    ):
        raise EngineError(
            "INCOMPARABLE", "Retention requires identical experiment, dataset, period and interval"
        )
    if any(r["period"] != "build" for r in results):
        raise EngineError(
            "SEALED_SELECTION", "Holdout results cannot be used for discovery retention"
        )

    def score(r):
        annual = r["metrics"]["annualized_return"]
        return (
            -(annual if annual is not None else float("inf")),
            r["metrics"]["max_drawdown"],
            r["run_id"],
        )

    ordered = sorted([r for r in results if r["qualified"]], key=score)
    kept, comparisons, rejected = [], [], []
    for candidate in ordered:
        duplicate = None
        for other in kept:
            pair = pd.concat(
                [
                    pd.Series(candidate["daily_returns"], name="a"),
                    pd.Series(other["daily_returns"], name="b"),
                ],
                axis=1,
            ).dropna()
            comparable = len(pair) >= rules.correlation_min_days and (pair.std() > 0).all()
            correlation = float(pair.a.corr(pair.b)) if comparable else None
            comparisons.append(
                {
                    "a": candidate["run_id"],
                    "b": other["run_id"],
                    "days": len(pair),
                    "correlation": correlation,
                    "status": "compared" if comparable else "not_comparable",
                }
            )
            if correlation is not None and correlation > rules.correlation_limit:
                duplicate = other["run_id"]
                break
        if duplicate:
            rejected.append({"run_id": candidate["run_id"], "duplicate_of": duplicate})
        else:
            kept.append(candidate)
    return {
        "kept": [r["run_id"] for r in kept],
        "duplicates": rejected,
        "comparisons": comparisons,
        "failed_rules": [r["run_id"] for r in results if not r["qualified"]],
    }
