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
    import am_backtesting.application as module

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


@pytest.fixture
def library(service):
    from am_backtesting import BacktestService

    return BacktestService(service.datasets.root, service.state, service.config)


def test_direct_and_http_independent_execution_identical(library, tmp_path, strategy, monkeypatch):
    from am_backtesting import BacktestService
    from am_backtesting.models import BacktestResult
    import am_backtesting.application as application

    other = BacktestService(library.datasets.root, tmp_path / "http-state", library.config)
    (other.state / "proof.json").write_text((library.state / "proof.json").read_text())
    experiment = ExperimentCreate(experiment_id="parity")
    direct_experiment = library.create(experiment)
    client = TestClient(create_app(other))
    assert client.post("/v1/experiments", json=experiment.model_dump(mode="json")).json() == (
        direct_experiment.model_dump(mode="json")
    )
    request = RunRequest(experiment_id="parity", candidate_id="same", strategy=strategy)
    calls = []
    original = application.simulate

    def counted(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(application, "simulate", counted)
    direct = library.run(request)
    response = client.post("/v1/backtests", json=request.model_dump(mode="json"))
    assert response.status_code == 200, response.text
    assert isinstance(direct, BacktestResult)
    assert len(calls) == 2  # Independent stores: HTTP cannot simply replay the direct result.
    assert response.json() == direct.model_dump(mode="json")
    assert direct.fills and direct.costs.fees > 0
    assert library.run(request) == direct
    assert library.get_result(direct.run_id) == direct
    assert library.get_experiment("parity") == direct_experiment
    assert len(calls) == 2  # Identical retries use persisted results.


@pytest.mark.parametrize("failure", ["sealed", "range", "missing", "proof", "busy", "reuse"])
def test_library_and_http_engine_errors_equivalent(library, strategy, failure):
    library.create(ExperimentCreate(experiment_id="errors"))
    request = RunRequest(experiment_id="errors", candidate_id="a", strategy=strategy)
    if failure == "sealed":
        request = request.model_copy(update={"period": "test"})
        library.datasets._read = lambda *a: pytest.fail("holdout file read attempted")
    elif failure == "range":
        request = RunRequest(
            experiment_id="errors",
            candidate_id="a",
            strategy=strategy,
            end="2024-10-06T00:00:00Z",
        )
    elif failure == "missing":
        request = request.model_copy(update={"experiment_id": "missing"})
    elif failure == "proof":
        (library.state / "proof.json").write_text('{"passed": false}')
    elif failure == "busy":
        library.lock.acquire()
    elif failure == "reuse":
        library.run(request)
        request = request.model_copy(
            update={"strategy": strategy.model_copy(update={"fraction": 0.2})}
        )
    try:
        with pytest.raises(EngineError) as caught:
            library.run(request)
        response = TestClient(create_app(library)).post(
            "/v1/backtests", json=request.model_dump(mode="json")
        )
        assert response.status_code == caught.value.status
        assert response.json() == {"code": caught.value.code, "message": caught.value.message}
    finally:
        if failure == "busy":
            library.lock.release()


@pytest.mark.parametrize("invalid", ["same_open", "negative_lag", "unknown_feature", "zero_fee"])
def test_library_and_http_validation_errors_equivalent(library, strategy, invalid):
    from pydantic import ValidationError

    payload = RunRequest(
        experiment_id="validation", candidate_id="a", strategy=strategy
    ).model_dump(mode="json")
    model, endpoint = RunRequest, "/v1/backtests"
    if invalid == "same_open":
        payload["strategy"]["execution"] = "same_open"
    elif invalid == "negative_lag":
        payload["strategy"]["entry"] = {
            "op": "lag",
            "periods": -1,
            "args": [{"op": "field", "name": "close"}],
        }
    elif invalid == "unknown_feature":
        payload["strategy"]["entry"] = {"op": "feature", "name": "missing"}
    else:
        payload = {"experiment_id": "validation", "rules": {"venue": {"fee_bps": 0}}}
        model, endpoint = ExperimentCreate, "/v1/experiments"
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    response = TestClient(create_app(library)).post(endpoint, json=payload)
    assert response.status_code == 422
    direct_errors = [(list(e["loc"]), e["type"], e["msg"]) for e in caught.value.errors()]
    http_errors = [(e["loc"][1:], e["type"], e["msg"]) for e in response.json()["detail"]]
    assert http_errors == direct_errors


def test_library_and_http_retention_identical(library, strategy):
    from am_backtesting.models import CorrelationRequest, RetentionResult

    library.create(ExperimentCreate(experiment_id="retain"))
    runs = [
        library.run(RunRequest(experiment_id="retain", candidate_id=c, strategy=strategy))
        for c in ["a", "b"]
    ]
    request = CorrelationRequest(run_ids=[r.run_id for r in runs])
    direct = library.retention(request)
    assert isinstance(direct, RetentionResult)
    response = TestClient(create_app(library)).post(
        "/v1/retention", json=request.model_dump(mode="json")
    )
    assert response.status_code == 200
    assert response.json() == direct.model_dump(mode="json")


def test_stale_proof_fingerprint_rejected_by_both_paths(library, strategy):
    report_path = library.state / "proof.json"
    report = json.loads(report_path.read_text())
    report["engine_sha256"] = "stale-engine"
    report_path.write_text(json.dumps(report))
    request = RunRequest(experiment_id="one", candidate_id="a", strategy=strategy)
    with pytest.raises(EngineError) as caught:
        library.run(request)
    response = TestClient(create_app(library)).post(
        "/v1/backtests", json=request.model_dump(mode="json")
    )
    assert caught.value.code == "PROOF_REQUIRED"
    assert response.status_code == 409
    assert response.json()["code"] == caught.value.code


def test_cli_preparation_and_explicit_unlock_use_only_synthetic_data(prepared, tmp_path):
    import subprocess
    import sys

    target = tmp_path / "cli-data"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "am_backtesting",
            "prepare",
            "--source",
            str(prepared.parent / "raw"),
            "--destination",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert set(json.loads(result.stdout)) == {"modern", "xmr"}
    command = [
        sys.executable,
        "-m",
        "am_backtesting",
        "unlock",
        "--sealed",
        str(target / "sealed"),
        "--destination",
        str(target / "unlocked"),
        "--track",
        "modern",
    ]
    denied = subprocess.run(command, capture_output=True, text=True)
    assert denied.returncode == 1
    assert json.loads(denied.stdout)["code"] == "ACK_REQUIRED"
    assert not (target / "unlocked/modern").exists()
    allowed = subprocess.run(
        command + ["--acknowledge-unseen-data-is-spent"], check=True, capture_output=True, text=True
    )
    assert json.loads(allowed.stdout)["unseen_data_spent"] is True
    assert (target / "unlocked/modern/unlock.json").exists()
