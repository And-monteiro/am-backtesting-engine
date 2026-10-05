import json
from pathlib import Path

from am_backtesting.models import RunRequest


def test_both_agreed_examples_validate():
    for filename in ("crossover.json", "momentum.json"):
        request = RunRequest.model_validate(json.loads((Path("examples") / filename).read_text()))
        assert request.period == "build"


def test_output_contract_documents_audit_information():
    from am_backtesting.api import app

    schemas = app.openapi()["components"]["schemas"]
    properties = schemas["BacktestResult"]["properties"]
    for field in [
        "fills",
        "metrics",
        "data_quality",
        "sealed_data_used",
        "policy_id",
        "dataset_id",
        "costs",
    ]:
        assert field in properties
