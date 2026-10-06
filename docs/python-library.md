# Python library contract and boundaries

The package remains `am-backtesting-engine`. Its stable entrypoint is `am_backtesting.BacktestService`, equivalently `am_backtesting.application.BacktestService`. The typed input/output models in `am_backtesting.models` are shared with the existing HTTP contract; there are no HTTP Request/Response objects in the application or simulation.

| Python method | Input | Output |
| --- | --- | --- |
| `BacktestService(data, state, config)` | Explicit `pathlib.Path` roots | Application instance with dataset store, SQLite repository and per-instance lock |
| `create(request)` | `ExperimentCreate` | `ExperimentResponse`; immutable persisted policy and dataset identity |
| `get_experiment(id)` | Experiment ID | `ExperimentResponse` |
| `run(request)` | `RunRequest` | `BacktestResult`; synchronous persisted simulation |
| `get_result(id)` | Run ID | `BacktestResult` |
| `retention(request)` | `CorrelationRequest` | `RetentionResult`; build-only retention |
| `require_proof()` | None | Current passing proof dictionary, or `EngineError` |

Use `.model_validate` or `.model_validate_json` on input models to deserialize external configuration. These produce Pydantic `ValidationError` for the same schema violations that HTTP returns as 422. Application failures raise `am_backtesting.application.EngineError`: its `code` and `message` are transport-independent; the retained `status` metadata supplies the unchanged HTTP mapping. FastAPI owns serialization and its validation error envelope. `.model_dump(mode="json")` on a result gives its HTTP JSON representation, including UTC timestamps and optional fields.

The programmatic entrypoint takes validated typed models, not raw mappings or bypassed `model_construct` objects. Reuse one instance in a single worker; separate instances or processes do not coordinate simulations. Original `am_backtesting.service.Service` callers can retain their dictionary outputs while migrating to the typed API.

## Module ownership

| Concern | Modules |
| --- | --- |
| Simulation, execution, cash/portfolio accounting | `engine.py` |
| In-memory candle validation and causal grids | `candles.py` |
| Feature/expression evaluation | `features.py` |
| Metrics, frozen-rule qualification and retention | `evaluate.py`, `models.py` |
| Application orchestration and policy/proof gates | `application.py` |
| SQLite persistence and audit attempts | `storage.py` |
| Filesystem/CSV access, partition preparation, explicit unlock | `data.py` |
| Synthetic fixtures and proof | `proof.py` |
| HTTP deserialization, error translation, response serialization | `api.py` |
| Argument parsing, admin dispatch, console output/exit codes | `cli.py`, `__main__.py` |
| Original internal API compatibility | `service.py` |

Before the refactor, `service.py` already orchestrated simulation, but was not a supported typed library API, HTTP read its repository directly, and the core imported candle processing from the filesystem module. After the refactor, HTTP uses application methods for reads and execution, the public API returns structured models, and candle processing is separate from disk access. The CLI continues to dispatch to existing library administration functions (`data.prepare`, `data.unlock`, `proof.run_proof`) rather than duplicating backtesting; OpenAPI export necessarily loads the HTTP adapter only for that command. No new job system or infrastructure is added.

## Data boundary and compatibility

`BacktestService` reads only build and explicitly unlocked partitions through the same `DatasetStore`; test requests require a matching unlock receipt and build requests cannot cross the sealed boundary. The instance has no unlock or raw-data preparation method. Administrative preparation and unlock stay outside simulation. A Python caller with unrestricted host filesystem privileges is not a security sandbox: enforce the same mount/permission boundary as the container. Never give the Strategy Finder access to raw or sealed data.

Costs, execution timing, gap handling, rules, schema and HTTP semantics remain unchanged. The source fingerprint intentionally changes after a refactor, invalidating previous proof and affecting run identities. Rerun synthetic proof before use; previously stored results remain readable, but executing an old candidate ID with a different engine fingerprint requires a new candidate ID under the existing idempotency rules. No migration rewrites old proofs, results or audit records.

The repository's config files must be supplied as external deployment inputs when installing elsewhere; the constructor explicitly requires their location. Docker continues to provide them at `/app/config`, with only build/unlocked data mounted. No holdout unlock or real strategy run is part of refactor validation.
