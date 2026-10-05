# AM Backtesting Engine

Design-stage Python backtesting service for an AI-assisted Investment Strategy Finder. Intended repository: https://github.com/And-monteiro/am-backtesting-engine. No engine implementation or strategy runs have been authorized yet.

## Architecture and usage

Run the engine and strategist in separate Docker Compose containers. Start with synchronous REST requests: the strategist submits one declarative candidate and receives its completed result or a structured error. A batch of 25 is initially 25 sequential requests. No queue, polling protocol, or pub-sub broker is required initially. This is a proposed design, subject to end-to-end performance measurements after implementation is authorized.

The strategist owns candidate generation, the pre-test discovery ledger, family selection, mutation, search notes, and reports. The engine owns deterministic simulation, evaluation against frozen rules, data access, and reproducible result artifacts. The strategist must not receive a mount of sealed data. Container separation alone does not enforce sealing.

Define versioned OpenAPI and JSON schemas before implementing interfaces. Strategy definitions must support measurable features, filters, rankings, compound conditions, allocation, schedules, exits, and explicit missing-data semantics without Python plugins or arbitrary executable code. Inputs must identify the strategy, dataset version, evaluation policy, and permitted period. Outputs must identify the run and versions, execution status, metrics, individual rule outcomes, costs, trades, equity/return series, and data-quality diagnostics. Failures must be distinguishable from tested strategies that fail evaluation rules.

An hourly close becomes available only at that hour's end; the CSV timestamp identifies the hour's open. Decisions use completed bars and execute at the next available observed open. Freeze request limits and timeouts after benchmarking complete simulations, including costs and result serialization. Load permitted data once and reuse it rather than reparsing all CSVs per candidate. Persist completed results keyed by reproducible request identity so retries can reuse them.

## Evaluation and data gaps

[config/rules.md](config/rules.md) records the mandatory evaluation requirements and the proposed causal gap policy. Small gaps must not discard entire testing windows. Estimated prices must be flagged and must not be used as executable market prices. Never interpolate using a future observation or across a sealed-period boundary.

[docs/data-inspection.md](docs/data-inspection.md) records the dataset inspection and the evidence supporting synchronous requests. HYPE was removed at the user's request; the remaining 15 CSVs are unchanged. XMR's February 2024 endpoint represents cessation of trading, as confirmed by the user, and must not be filled as a data gap. The evaluation policy is not yet frozen: required numerical choices remain unresolved. Real strategy runs are blocked until the policy is completed and the required synthetic proof is passed and reported.
