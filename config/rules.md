# Rules before results

Status: DESIGN DRAFT, NOT FROZEN. No strategy runs are permitted until the unresolved values below are selected and frozen. Examples supplied in discussion are not selected defaults. This document specifies requirements; enforcement is not implemented.

## Mandatory evaluation rules

- Market: crypto. Timeframe: hourly.
- Universe: exclude HYPE; its CSV was deleted at the user's request. Retain XMR's historical records. The user confirmed that its February 2024 endpoint reflects cessation of trading, not missing data. No gap filling or trading may extend beyond its trading availability. Terminal-position treatment must be frozen before runs; do not assume an executable liquidation price or use hindsight to exit before cessation.
- Minimum completed trades before results count: UNSET. Define completed-trade counting before freezing.
- Costs apply on every trade, on each side. Fee rate: UNSET. Spread/slippage model: UNSET. Financing applies to anything borrowed; annual rate and accrual convention: UNSET.
- Benchmark: UNSET. Beating it requires BOTH better return AND smaller worst drawdown. Freeze return and drawdown definitions and use comparable periods and cost treatment.
- Consistency: positive net return in at least two of three equal chronological chunks of the evaluated history. Freeze boundaries and boundary accounting before results.
- Correlation: strategies moving together with correlation greater than 0.7 count as one; keep the better. Freeze return sampling, overlapping-history requirements, the better-strategy criterion, and tie-breaking before results.
- These rules do not change after results are seen. A separately identified future experiment cannot relabel prior results under revised rules.

## Timing and sealed history

- Decide on the close and trade at the next open; never use future information.
- Costs are always enabled.
- Split history into build and sealed test periods before strategy runs. Boundaries: UNSET. Nothing may read the sealed test period until the user explicitly unlocks it.
- Discovery runs use only the build period. Never use sealed observations to fill gaps or compute features in the build period.

## Missing hourly observations

User requirement: tolerate small gaps, including three or four consecutive missing hours, rather than losing entire testing windows. Proposed implementation policy below must be finalized before runs:

- Keep raw CSVs immutable and construct an explicit hourly UTC timeline. Missing hours must not compress time or change an hours-based lookback into an observation-count lookback.
- For up to four elapsed consecutive missing hours, carry forward the most recent observed close as an estimated price for continuity and valuation. If OHLC placeholders are needed, all four values equal that last observed close. Never interpolate toward a future observed price.
- Mark each placeholder as synthetic, with gap age and source timestamp. Volume and trade counts are unknown, not invented activity or assumed zero. Rules needing unavailable activity inputs cannot fire.
- Do not generate new trading decisions, fill orders, or trigger stops on synthetic bars. Skip those execution opportunities and resume on observed data. Any still-valid pending order can execute no earlier than the next observed open, subject to its expiry. Do not invent intragap fills or stop crossings.
- Apply the four-hour allowance incrementally using only the elapsed gap age. Do not inspect the future gap length to decide how to treat earlier hours.
- Beyond four hours, do not continue supplying filled prices to strategy features. Suspend affected-asset decisions and require adequate valid lookback data before resuming. Retain unaffected assets and usable history; do not silently discard the entire window. Continue elapsed-time financing on existing borrowing.
- Existing positions cannot disappear during a gap. Any last-price valuation is explicitly stale; unresolved exposure, end-of-file positions, and material intragap risk must be reported. The exact long-gap and terminal-position accounting policy remains UNSET and blocks policy freezing.
- Do not fill before an asset's first observation or extend its tradable history beyond its final observation. Different asset coverage is not an internal gap.
- Report gap counts, missing hours, synthetic hours used, skipped decisions/executions, stale valuations, and affected assets. Apply consistent time alignment to benchmark comparisons.

## Required engine proof and stop

Before any real strategy runs:

1. Plant a known edge in synthetic prices and confirm the engine finds it.
2. Run pure noise and confirm the engine finds nothing.
3. Feed it a signal that cheats by using the same day's close and confirm it gets caught.

Freeze fixtures, statistical acceptance criteria for the noise check, and precise prohibited timing for the cheating check before executing them. No fixtures or checks have been run. These implementation details must preserve the user's three required outcomes.

If any check fails, stop and tell the user. Do not continue to strategies. Print the rules and all three check results, then stop even if all pass.
