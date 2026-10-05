import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .models import ExperimentCreate
from .util import EngineError, fingerprint


class Repository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS attempts (experiment TEXT NOT NULL, candidate TEXT NOT NULL, identity TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, error TEXT, PRIMARY KEY(experiment,candidate))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, experiment TEXT NOT NULL, candidate TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(experiment,candidate))"
            )

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, experiment: str, candidate: str, identity: str, request: dict):
        with self.connect() as db:
            row = db.execute(
                "SELECT identity FROM attempts WHERE experiment=? AND candidate=?",
                (experiment, candidate),
            ).fetchone()
            if row and row[0] != identity:
                raise EngineError(
                    "CANDIDATE_REUSED", "Candidate already logged with different request", 409
                )
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?) ON CONFLICT(experiment,candidate) DO UPDATE SET status='running',error=NULL",
                (
                    experiment,
                    candidate,
                    identity,
                    json.dumps(request, allow_nan=False),
                    "running",
                    None,
                ),
            )

    def attempt_status(self, experiment: str, candidate: str, status: str, error=None):
        with self.connect() as db:
            db.execute(
                "UPDATE attempts SET status=?,error=? WHERE experiment=? AND candidate=?",
                (status, error, experiment, candidate),
            )

    def create_experiment(self, request: ExperimentCreate, dataset_id: str):
        payload = request.model_dump(mode="json")
        payload["dataset_id"] = dataset_id
        payload["policy_id"] = fingerprint(payload)
        text = json.dumps(payload, sort_keys=True, allow_nan=False)
        with self.connect() as db:
            old = db.execute(
                "SELECT payload FROM experiments WHERE id=?", (request.experiment_id,)
            ).fetchone()
            if old:
                if old[0] != text:
                    raise EngineError(
                        "FROZEN_POLICY",
                        "Experiment already exists with different settings; create a new ID",
                        409,
                    )
            else:
                db.execute("INSERT INTO experiments VALUES (?,?)", (request.experiment_id, text))
        return payload

    def experiment(self, experiment_id: str):
        with self.connect() as db:
            found = db.execute(
                "SELECT payload FROM experiments WHERE id=?", (experiment_id,)
            ).fetchone()
        if not found:
            raise EngineError("NOT_FOUND", "Unknown experiment", 404)
        return json.loads(found[0])

    def cached(self, experiment_id: str, candidate: str, identity: str):
        with self.connect() as db:
            found = db.execute(
                "SELECT identity,payload FROM runs WHERE experiment=? AND candidate=?",
                (experiment_id, candidate),
            ).fetchone()
        if found and found[0] != identity:
            raise EngineError(
                "CANDIDATE_REUSED",
                "Candidate ID already identifies a different request; use a new ID",
                409,
            )
        return json.loads(found[1]) if found else None

    def save(self, result: dict, identity: str):
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?)",
                (
                    result["run_id"],
                    result["experiment_id"],
                    result["candidate_id"],
                    identity,
                    json.dumps(result, allow_nan=False),
                ),
            )

    def run(self, run_id: str):
        with self.connect() as db:
            found = db.execute("SELECT payload FROM runs WHERE id=?", (run_id,)).fetchone()
        if not found:
            raise EngineError("NOT_FOUND", "Unknown run", 404)
        return json.loads(found[0])
