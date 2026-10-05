# Rules before results

Version 1.0. Agreed defaults are frozen before the first synthetic proof or real strategy run. Each experiment stores an immutable policy fingerprint. A different drawdown limit requires a new experiment ID and never retroactively changes an earlier result.

## Qualification

- Crypto, hourly, spot, long-only, no borrowing, no shorting. Starting balance: 1,000 USDT. Reject buys without sufficient cash including costs. Financing is zero because borrowing is forbidden.
- Minimum trades: 300 completed position round trips across the build-period portfolio. Partial exits do not add completed trades. A position completes when its remaining balance is below its instrument quantity increment. Report dust and outstanding holdings.
- Primary profile: Revolut X Portugal/EEA, 9 basis points taker fees per side plus at least 5 basis points adverse execution per side. Secondary comparison: Alpaca, fixed entry-tier 25 basis points taker fees per side, charged in the received asset, plus at least 5 basis points adverse execution. Fixed current profiles are scenario assumptions across historical Binance prices, not historical venue fee claims. Fees and execution allowance cannot be disabled.
- Benchmark: an assumed cash account earning 2% effective annually, compounded continuously in elapsed time using a 365.25-day year. Trading-account idle cash earns zero unless a later separately agreed policy changes this. USDT, USDC, and USD are not interchangeable. No conversions are inferred.
- Qualification requires net annualized return strictly greater than 2% AND worst drawdown strictly below 20% by default. The user approved replacing the original smaller-drawdown-than-benchmark rule because deterministic yielding cash has zero drawdown. The drawdown threshold is configurable when creating a new experiment.
- Worst drawdown: maximum proportional decline in hourly marked equity from its prior peak, including starting capital. Hourly OHLC cannot establish intrahour portfolio drawdown.
- Consistency: positive net equity returns in at least two of three equal chronological chunks. If the hour count is not divisible by three, earlier chunks get the extra hours. Carry holdings and costs across chunk boundaries; do not restart capital.
- Correlation: daily UTC net portfolio returns including idle days, identical experiment/data/evaluation intervals, at least 90 overlapping days, positive (not absolute) Pearson correlation greater than 0.7. Zero-variance or short comparisons are not comparable. Keep the qualifying strategy with greater annualized net return, then lower drawdown, then lexicographically smaller run ID. Deterministic greedy retention checks each candidate against already retained strategies; report every comparison.
- None of these rules changes after results are seen. New configurations require separately identified experiments. Qualification is build-period screening, not proof of future income.

## Execution and data

- Decide using completed candles at their close and execute no earlier than the next observed hourly open. CSV timestamps identify hourly opens, not closes. A close at 01:00 can place an order at the observed 01:00 open of the next candle. Same-candle open execution and negative lookbacks are prohibited.
- Market orders only. Orders expire after four elapsed hours by default, configurable per strategy before testing. Fixed-fraction sizing is set at entry; ranked portfolios rebalance toward equal weights on their UTC schedule. Alphabetical symbols break ranking ties. Sells execute before buys. No deposits, withdrawals, lending, staking, or invented yield.
- Small internal gaps: carry the last observed close for up to four elapsed missing hours for price-feature continuity and valuation. Mark synthetic bars; unknown volume/trade counts stay unknown. Skip decisions and execution on synthetic bars. Never interpolate toward future data.
- Longer gaps: price features become unavailable after hour four and require sufficient valid lookback on recovery. Existing holdings remain valued at their last observed close, explicitly stale; no fictional liquidation during a gap. Report stale exposure, synthetic hours, skipped decisions, expired/rejected orders, and unresolved positions. Strategies with stale holdings at evaluation end cannot qualify. Missing observations never compress elapsed time.
- Retained universe: 15 assets; HYPE excluded. Asset history does not extend before its first observation or after its trading availability. Preserve XMR historical data.
- XMR: assume a scheduled terminal liquidation at the final recorded open, 2024-02-20 02:00 UTC, with fees and adverse execution. Cancel outstanding orders and prohibit subsequent trading; end its evaluation after the final recorded hour. This is an explicitly assumed cessation event, not a verified historical announcement or guaranteed real fill. Terminal liquidation closes fractional dust without ordinary order-minimum constraints. Other assets remain marked at the evaluation end, without forced discretionary exits.
- Binance USDT OHLC is the price source. Use the committed current Revolut EEA snapshot for base quantity increments/minima/maxima where a corresponding USDC pair exists. Historical availability is not inferred. Otherwise research quantity constraints default to 1e-8 units. Minimum notional 1 USDT and maximum notional 200,000 USDT remain quote-currency research proxies. Alpaca uses research quantity constraints pending an authenticated account snapshot. Manifests and results identify constraint sources. Do not claim Binance candles reproduce Revolut X or Alpaca execution, supported assets, or liquidity.

## Build and sealed periods

- Modern track: build before 2024-10-05 00:00 UTC; sealed from that instant through 2026-10-04 23:00's candle (exclusive end 2026-10-05 00:00 UTC). XMR is excluded from this track.
- XMR track: build before 2022-02-20 00:00 UTC; sealed from that instant through its final 2024-02-20 02:00 candle (exclusive end 03:00 UTC). Only XMR is included, so later modern build data cannot contaminate this historical experiment.
- Preparation is an administrative streaming partition, not strategy evaluation. The earlier completeness inspection is disclosed. The API container mounts only build and explicitly unlocked files; sealed files and raw prices are not mounted. There is no API unlock endpoint.
- Only the user may explicitly unlock a track through the administrative CLI. Unlocking permanently spends its unseen status. Test requests before unlock fail. Warmup can use only preceding permitted build history. No features or gap repair cross into sealed observations during discovery.

## Required proof and stop

Before real strategies, run the fixed criteria in config/proof.json. No random seed regeneration or selection after results:

1. Plant a known observable edge in synthetic prices; require at least 300 completed trades, more than 50% net return, qualification, next-open timing, and agreement with independent accounting within 1e-6 USDT.
2. Run pure independent noise for 20 fixed seeds and three predefined rules (60 trials); require all trials to have at least 300 completed trades, no qualifying runs, nonpositive mean net return, and no more than half positive trials. The bounded-noise fixture is deliberately smaller than round-trip costs; this proves these fixtures do not acquire an artificial edge, not that random markets can never produce profitable strategies.
3. Reject future-close fields, negative lags, and same-open overrides. Also verify a legitimate close-based signal executes at the next observed open using that open's price.

If any check fails, stop and report it; do not continue to real strategies. Print these rules and all three results, then stop. Synthetic checks are part of the implementation test suite and must pass before code is pushed/merged. Service runs are blocked without a passing report matching engine source, rules, defaults, and proof criteria. No real strategies are authorized by implementation approval.
