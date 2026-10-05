import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from am_backtesting.api import create_app
from am_backtesting.data import DatasetStore, prepare, unlock
from am_backtesting.models import ExperimentCreate, RunRequest
from am_backtesting.proof import run_proof, synthetic_frame
from am_backtesting.service import Service
from am_backtesting.util import EngineError


@pytest.fixture(scope="session")
def proof_report():
    return run_proof(Path("config"))


@pytest.fixture
def prepared(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    for asset, date in [("BTCUSDT", "2024-10-04T20:00:00Z"), ("XMRUSDT", "2022-02-19T20:00:00Z")]:
        frame = synthetic_frame(np.full(10, 100.0), np.full(10, 100.0), np.ones(10))
        frame.index = pd.date_range(date, periods=10, freq="h")
        frame.reset_index(names="open_time_utc").to_csv(raw / f"{asset}_WITHGAP.csv", index=False)
    data = tmp_path / "data"
    prepare(raw, data)
    return data


@pytest.fixture
def service(tmp_path, prepared, proof_report):
    state = tmp_path / "state"
    state.mkdir()
    (state / "proof.json").write_text(json.dumps(proof_report), encoding="utf-8")
    return Service(prepared, state, Path("config"))


def test_mandatory_proof(proof_report):
    assert proof_report["passed"], proof_report["checks"]
    assert len(proof_report["checks"]) == 3
    assert not proof_report["sealed_data_read"]


def test_sealed_rejected_before_file_access(prepared, monkeypatch):
    store = DatasetStore(prepared)
    monkeypatch.setattr(store, "_read", lambda *a: pytest.fail("sealed file read attempted"))
    with pytest.raises(EngineError, match="unlock"):
        store.load("modern", "test", ["BTCUSDT"])


def test_build_range_cannot_cross_seal(prepared):
    with pytest.raises(EngineError):
        DatasetStore(prepared).load(
            "modern", "build", ["BTCUSDT"], end=pd.Timestamp("2024-10-06T00:00Z")
        )


def test_xmr_restricted_to_historical_track(prepared):
    store = DatasetStore(prepared)
    with pytest.raises(EngineError):
        store.load("modern", "build", ["XMRUSDT"])
    with pytest.raises(EngineError):
        store.load("xmr", "build", ["BTCUSDT"])


def test_build_load_does_not_open_sealed(prepared, monkeypatch):
    store = DatasetStore(prepared)
    original = store._read

    def checked(filename, *args):
        assert "sealed" not in filename and "unlocked" not in filename
        return original(filename, *args)

    monkeypatch.setattr(store, "_read", checked)
    frames, _, _, _, end, _ = store.load("modern", "build", ["BTCUSDT"])
    assert len(frames["BTCUSDT"]) == 4
    assert end == pd.Timestamp("2024-10-05T00:00Z")


def test_explicit_unlock_and_build_warmup(prepared):
    with pytest.raises(EngineError):
        unlock(prepared / "sealed", prepared / "unlocked", "modern", False)
    unlock(prepared / "sealed", prepared / "unlocked", "modern", True)
    frames, _, _, start, _, _ = DatasetStore(prepared).load(
        "modern", "test", ["BTCUSDT"], end=pd.Timestamp("2024-10-05T06:00Z")
    )
    assert start == pd.Timestamp("2024-10-05T00:00Z")
    assert len(frames["BTCUSDT"]) == 10


def test_tampered_csv_rejected(prepared):
    file = prepared / "build/modern/BTCUSDT.csv"
    file.write_text(file.read_text().replace("100.0", "99.0"), encoding="utf-8")
    with pytest.raises(EngineError, match="fingerprint"):
        DatasetStore(prepared).load("modern", "build", ["BTCUSDT"])


def test_prepare_refuses_overwrite(prepared):
    with pytest.raises(EngineError):
        prepare(prepared.parent / "raw", prepared)


def test_immutable_experiments(service):
    one = service.create(ExperimentCreate(experiment_id="one"))
    assert service.create(ExperimentCreate(experiment_id="one")) == one
    with pytest.raises(EngineError, match="different"):
        service.create(
            ExperimentCreate.model_validate(
                {"experiment_id": "one", "rules": {"max_drawdown": 0.3}}
            )
        )
    two = service.create(
        ExperimentCreate.model_validate({"experiment_id": "two", "rules": {"max_drawdown": 0.3}})
    )
    assert one["policy_id"] != two["policy_id"]


def test_api_sync_result_and_retry(service, strategy):
    client = TestClient(create_app(service))
    assert client.get("/health").status_code == 200
    assert client.post("/v1/experiments", json={"experiment_id": "one"}).status_code == 200
    payload = RunRequest(
        experiment_id="one", candidate_id="candidate", strategy=strategy
    ).model_dump(mode="json")
    response = client.post("/v1/backtests", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert not result["sealed_data_used"]
    assert result["status"] == "completed"
    assert client.post("/v1/backtests", json=payload).json() == result
    assert client.get("/v1/backtests/" + result["run_id"]).json() == result
    payload["strategy"]["fraction"] = 0.2
    assert client.post("/v1/backtests", json=payload).status_code == 409


def test_candidate_logged_before_simulation(service, strategy, monkeypatch):
    service.create(ExperimentCreate(experiment_id="one"))
    import am_backtesting.service as module

    original = module.simulate

    def audited(*args):
        with service.repo.connect() as db:
            row = db.execute(
                "SELECT status,request FROM attempts WHERE experiment='one' AND candidate='a'"
            ).fetchone()
        assert row and row[0] == "running"
        assert json.loads(row[1])["candidate_id"] == "a"
        return original(*args)

    monkeypatch.setattr(module, "simulate", audited)
    service.run(RunRequest(experiment_id="one", candidate_id="a", strategy=strategy))
    with service.repo.connect() as db:
        assert db.execute("SELECT status FROM attempts").fetchone()[0] == "completed"


def test_failed_attempt_remains_logged(service, strategy):
    service.create(ExperimentCreate(experiment_id="one"))
    with pytest.raises(EngineError):
        service.run(
            RunRequest(experiment_id="one", candidate_id="a", strategy=strategy, period="test")
        )
    with service.repo.connect() as db:
        status, error = db.execute("SELECT status,error FROM attempts").fetchone()
    assert status == "failed" and "unlock" in error


def test_api_rejects_same_open_override(service, strategy):
    client = TestClient(create_app(service))
    payload = RunRequest(experiment_id="one", candidate_id="a", strategy=strategy).model_dump(
        mode="json"
    )
    payload["strategy"]["execution"] = "same_open"
    assert client.post("/v1/backtests", json=payload).status_code == 422


def test_failed_proof_blocks_strategies(service, strategy):
    (service.state / "proof.json").write_text('{"passed": false}')
    with pytest.raises(EngineError, match="Proof failed"):
        service.run(RunRequest(experiment_id="one", candidate_id="a", strategy=strategy))


def test_busy_response(service, strategy):
    service.lock.acquire()
    try:
        with pytest.raises(EngineError) as caught:
            service.run(RunRequest(experiment_id="one", candidate_id="a", strategy=strategy))
        assert caught.value.status == 429
    finally:
        service.lock.release()


def test_openapi_has_no_unlock_endpoint():
    schema = create_app().openapi()
    assert "/v1/backtests" in schema["paths"]
    assert not any("unlock" in path for path in schema["paths"])
    assert schema["components"]["schemas"]["Strategy"]["additionalProperties"] is False
