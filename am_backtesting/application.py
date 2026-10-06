"""Typed application API shared by Python callers and transport adapters."""

import json
import threading
from pathlib import Path

from . import __version__
from .data import DatasetStore
from .engine import simulate
from .evaluate import retain
from .models import (
    BacktestResult,
    CorrelationRequest,
    ExperimentCreate,
    ExperimentResponse,
    Instrument,
    RetentionResult,
    Rules,
    RunRequest,
)
from .storage import Repository
from .util import EngineError, engine_hash, file_hash, fingerprint


class BacktestService:
    """Run against prepared build/unlocked data with persisted policy, proof and audit gates.

    This API never prepares or unlocks holdouts. Callers must enforce the same filesystem
    permissions/mounts as the HTTP deployment; importing Python is not a sandbox.
    """

    def __init__(self, data: Path, state: Path, config: Path):
        self.datasets = DatasetStore(data)
        self.repo = Repository(state / "engine.sqlite")
        self.state, self.config = state, config
        self.lock = threading.Lock()

    def require_proof(self):
        path = self.state / "proof.json"
        if not path.exists():
            raise EngineError(
                "PROOF_REQUIRED", "Run the three synthetic proof checks before strategy runs", 409
            )
        report = json.loads(path.read_text(encoding="utf-8"))
        if (
            report.get("passed") is not True
            or report.get("engine_sha256") != engine_hash()
            or report.get("criteria_sha256") != file_hash(self.config / "proof.json")
            or report.get("defaults_sha256") != file_hash(self.config / "defaults.json")
            or report.get("rules_sha256") != file_hash(self.config / "rules.md")
        ):
            raise EngineError(
                "PROOF_REQUIRED", "Proof failed or code/rules changed; rerun proof", 409
            )
        return report

    def create(self, request: ExperimentCreate) -> ExperimentResponse:
        _, manifest = self.datasets.manifest(request.track, "build")
        return ExperimentResponse.model_validate(
            self.repo.create_experiment(request, manifest["dataset_id"])
        )

    def run(self, request: RunRequest) -> BacktestResult:
        self.require_proof()
        if not self.lock.acquire(blocking=False):
            raise EngineError("BUSY", "One synchronous simulation is running; retry later", 429)
        reserved = False
        try:
            experiment = self.repo.experiment(request.experiment_id)
            identity = fingerprint(
                {
                    "request": request.model_dump(mode="json"),
                    "policy_id": experiment["policy_id"],
                    "engine_sha256": engine_hash(),
                }
            )
            cached = self.repo.cached(request.experiment_id, request.candidate_id, identity)
            if cached:
                return BacktestResult.model_validate(cached)
            self.repo.reserve(
                request.experiment_id,
                request.candidate_id,
                identity,
                request.model_dump(mode="json"),
            )
            reserved = True
            loaded = self.datasets.load(
                experiment["track"],
                request.period,
                request.strategy.assets,
                request.start,
                request.end,
            )
            frames, instruments, terminal, start, end, manifest = loaded
            if experiment["dataset_id"] != manifest["dataset_id"]:
                raise EngineError(
                    "DATA_CHANGED", "Experiment dataset does not match mounted dataset", 409
                )
            rules = Rules.model_validate(experiment["rules"])
            constraints = {
                a: manifest["assets"][a]["constraints_source"] for a in request.strategy.assets
            }
            if rules.venue.name == "alpaca":
                instruments = {a: Instrument() for a in request.strategy.assets}
                constraints = {
                    a: "Alpaca research proxy; account-specific asset constraints require an authenticated snapshot"
                    for a in request.strategy.assets
                }
            result = simulate(request.strategy, rules, frames, start, end, instruments, terminal)
            result.update(
                {
                    "schema_version": "1.0",
                    "run_id": identity,
                    "experiment_id": request.experiment_id,
                    "candidate_id": request.candidate_id,
                    "policy_id": experiment["policy_id"],
                    "dataset_id": manifest["dataset_id"],
                    "strategy_id": fingerprint(request.strategy.model_dump(mode="json")),
                    "engine_version": __version__,
                    "engine_sha256": engine_hash(),
                    "period": request.period,
                    "track": experiment["track"],
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "sealed_data_used": request.period == "test",
                    "unseen_data_spent": request.period == "test",
                    "price_source": "Binance USDT",
                    "venue_profile": rules.venue.name,
                    "constraints": constraints,
                }
            )
            typed_result = BacktestResult.model_validate(result)
            self.repo.save(result, identity)
            self.repo.attempt_status(request.experiment_id, request.candidate_id, "completed")
            return typed_result
        except Exception as error:
            if reserved:
                self.repo.attempt_status(
                    request.experiment_id, request.candidate_id, "failed", str(error)
                )
            raise
        finally:
            self.lock.release()

    def get_experiment(self, experiment_id: str) -> ExperimentResponse:
        return ExperimentResponse.model_validate(self.repo.experiment(experiment_id))

    def get_result(self, run_id: str) -> BacktestResult:
        return BacktestResult.model_validate(self.repo.run(run_id))

    def retention(self, request: CorrelationRequest) -> RetentionResult:
        results = [self.repo.run(run_id) for run_id in request.run_ids]
        rules = Rules.model_validate(self.repo.experiment(results[0]["experiment_id"])["rules"])
        return RetentionResult.model_validate(retain(results, rules))
