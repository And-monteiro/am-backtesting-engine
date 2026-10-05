import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .models import (
    BacktestResult,
    CorrelationRequest,
    ErrorBody,
    ExperimentCreate,
    ExperimentResponse,
    RetentionResult,
    RunRequest,
)
from .service import Service
from .util import EngineError, config_root


def create_app(service: Service | None = None):
    app = FastAPI(
        title="AM Backtesting Engine",
        version=__version__,
        description="Synchronous declarative spot backtests. Sealed periods require explicit administrative unlock.",
    )
    app.state.service = service

    def get_service():
        if app.state.service is None:
            app.state.service = Service(
                Path(os.environ.get("AM_DATA_ROOT", "data")),
                Path(os.environ.get("AM_STATE_ROOT", "state")),
                config_root(),
            )
        return app.state.service

    @app.exception_handler(EngineError)
    def engine_error(request: Request, error: EngineError):
        return JSONResponse(
            status_code=error.status, content={"code": error.code, "message": error.message}
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/v1/proof", responses={409: {"model": ErrorBody}})
    def proof():
        return get_service().require_proof()

    @app.post(
        "/v1/experiments", response_model=ExperimentResponse, responses={409: {"model": ErrorBody}}
    )
    def create_experiment(request: ExperimentCreate):
        return get_service().create(request)

    @app.get("/v1/experiments/{experiment_id}", response_model=ExperimentResponse)
    def experiment(experiment_id: str):
        return get_service().repo.experiment(experiment_id)

    @app.post(
        "/v1/backtests",
        response_model=BacktestResult,
        responses={403: {"model": ErrorBody}, 409: {"model": ErrorBody}, 429: {"model": ErrorBody}},
    )
    def backtest(request: RunRequest):
        return get_service().run(request)

    @app.get("/v1/backtests/{run_id}", response_model=BacktestResult)
    def result(run_id: str):
        return get_service().repo.run(run_id)

    @app.post("/v1/retention", response_model=RetentionResult)
    def retention(request: CorrelationRequest):
        return get_service().retention(request.run_ids)

    return app


app = create_app()
