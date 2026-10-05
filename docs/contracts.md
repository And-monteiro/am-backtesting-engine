# Interface contracts, version 1.0

## HTTP

All endpoints use JSON. Prices, allocation fractions and returns are numeric; 0.2 means 20%. Fees are in basis points (1bps = 0.01%). Times are UTC or explicitly offset ISO-8601 values at exact UTC hourly boundaries. Evaluation start is inclusive and end exclusive. See `contracts/openapi.json` for every request/response schema. Unknown fields are rejected.

| Interface | Input | Output |
| --- | --- | --- |
| GET /health | None | Service status/version; not proof readiness |
| GET /v1/proof | None | Passing, current synthetic report; otherwise 409 |
| POST /v1/experiments | Experiment ID, track, complete or defaulted rules | Immutable experiment, policy ID, dataset ID |
| GET /v1/experiments/{id} | Experiment ID | Stored policy |
| POST /v1/backtests | Experiment ID, unique candidate ID, strategy, period, optional start/end | Completed synchronous result including metrics, individual rule outcomes, fills, positions, costs, equity, daily returns, data-quality diagnostics and reproducibility IDs |
| GET /v1/backtests/{run_id} | Result identity | Persisted result |
| POST /v1/retention | 2–100 unique completed build run IDs | Kept IDs, duplicates, comparisons and failed-rule IDs |

Backtest success is HTTP 200 with `status: completed`; `qualified: false` is a tested strategy that failed rules, not a calculation failure. Engine errors use `{code, message}`. HTTP 403 means sealed/range access denied; 404 unknown experiment/result; 409 immutable-policy conflict, reused candidate ID, changed data or missing proof; 422 unsupported/invalid request or data; 429 simulation busy. FastAPI schema-validation errors use its documented `detail` array with field locations. Unexpected server exceptions are failures, never qualifying results. Persisted attempt status/error is available in the administrative SQLite audit store.

Retries must preserve candidate identity and request. Failed or interrupted identical attempts can be retried; a changed strategy requires a new candidate ID. A single process/worker and private local deployment are supported. No user authentication or cross-instance job coordination is provided. The API logs attempts before testing; the strategist must also log candidates to its own `discovery/ledger.jsonl` before submission.

## Strategy semantics

- `signals`: fixed fraction of marked equity committed at entry, subject to cash, fees and quantity constraints. Entry can fire only when flat, and exit applies while holding. Entry has no precedence over an exit on an existing position. Maximum holding duration is measured in elapsed hours. `equal_weight` is reserved for ranking mode.
- `rank`: entry expression is eligibility, rank expression is numeric, and top-N selected assets receive equal target weights up to maximum exposure. Existing unavailable holdings reserve exposure. Nonselected observed holdings are sold; unavailable holdings cannot be sold at invented prices. Partial rebalances are not completed trades.
- `schedule`: evaluated at candle end. Daily/weekly scheduled decisions occur at the requested UTC hour/weekday; Monday 00:00 execution therefore uses Sunday's 23:00 candle close.
- `features`: windows use hourly candles or complete UTC daily aggregates. A 90-day return requires 91 daily closing prices. Volatility is sample standard deviation of simple returns, not annualized. An SMA of 20 and 50 on hourly data means 20 and 50 hours. Daily windows publish after the day ends.
- `expressions`: positive `lag.periods` shifts hourly evaluation series backward; arithmetic, comparisons and crossovers use only data known at that close. Logical operators require booleans. Undefined values do not trigger a condition, including under negation. Named features must exist. Maximum expression depth is 12 and total nodes 256; at most 32 features and 15 assets.
- Gaps retain a UTC grid; the first four missing internal price bars can carry the last close, while activity stays unknown. The fifth and subsequent missing price bars invalidate price lookbacks. Features may use marked synthetic prices, but orders/decisions never execute on synthetic observations. At normal evaluation end positions remain marked; only the declared XMR event forces liquidation.

## Result semantics

`net_return` includes all simulated fees and adverse execution. `annualized_return` uses elapsed hours and a 365.25-day year; extreme short-run overflow is represented by null with an explicit overflow flag. Maximum drawdown includes initial equity. Thresholds are strict; drawdown equality within 1e-12 does not pass. Benchmark is 2% effective annual growth over exactly the same elapsed duration; trading cash does not earn that yield.

Equity points are timestamped at candle close. Daily returns are sampled by UTC trading day; the first daily return starts from initial capital. Monthly returns include partial first/last months. `realized_pnl` sums completed position round trips; it excludes open holdings and separately carried dust. Fee-created fractional dust is preserved in equity and `dust_positions`, not erased; an active position closes when its residual is less than one quantity increment. Ending stale exposure prevents qualification.

`policy_id`, `dataset_id`, `strategy_id`, engine version/source hash, and run ID identify reproducibility. Results disclose `sealed_data_used`, `unseen_data_spent`, price source, execution profile, constraint sources, and assumed terminal settlement. Build qualification applies minimum trades, net return, drawdown, consistency and stale-exposure checks. Correlation retention is a separate portfolio-level comparison, not a single-run pass/fail test.

## CSV and administrative data contract

Local input filenames are `<ASSET>USDT_WITHGAP.csv`. Exact header:

```text
open_time_utc,open,high,low,close,volume_base,volume_quote,trades
```

Timestamps are ascending unique hourly UTC opens. Prices are finite positive numbers with low <= open/close <= high; volumes and trade counts are nonnegative, with integral trade counts. Preparation rejects timestamp/schema failures; the service validates permitted price data and hashes on load. Source files remain unchanged. HYPE is excluded.

`prepare --source prices --destination data` streams into build/sealed CSVs, writes versioned manifests with source/partition hashes, coverage/terminal metadata, quantity constraints and sources, and creates an empty unlocked directory. An existing prepared dataset cannot be overwritten. The service only opens its mounted build/unlocked folders, validating manifest identities and CSV hashes. There is no raw CSV upload endpoint or raw-directory mount in the API container.

`unlock --track modern|xmr --acknowledge-unseen-data-is-spent` is an explicit user administrative action that copies a sealed track to the unlocked directory and writes a dataset-specific receipt. The API cannot perform it. A test run can use permitted preceding build history as warmup; no build run reads holdout prices. Both tracks use the boundaries recorded in `config/rules.md`.

SQLite contracts: immutable `experiments`, pre-execution `attempts` with request/identity/status/error, and immutable completed `runs`. These are internal administrative records; the HTTP contracts are the supported integration interface.
