import ast
import json
import subprocess
import sys
from pathlib import Path


def test_library_import_and_simulation_without_transport_modules():
    code = """
import sys
sys.modules.update({name: None for name in ('fastapi', 'uvicorn', 'argparse', 'am_backtesting.data', 'am_backtesting.storage')})
from am_backtesting.engine import simulate
from am_backtesting.models import Expression, Rules, Strategy
from am_backtesting.proof import synthetic_frame
import numpy as np
frame = synthetic_frame(np.full(3, 100.), np.full(3, 100.), np.ones(3))
strategy = Strategy(name='boundary', assets=['BTCUSDT'], exit=Expression(op='constant', value=False))
assert simulate(strategy, Rules(), {'BTCUSDT': frame}, frame.index[0], frame.index[-1] + __import__('pandas').Timedelta(hours=1))['status'] == 'completed'
del sys.modules['am_backtesting.data']
del sys.modules['am_backtesting.storage']
from am_backtesting import BacktestService
assert BacktestService.__module__ == 'am_backtesting.application'
"""
    subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True)


def test_core_and_application_do_not_import_adapters():
    for name in ["engine", "candles", "features", "evaluate", "models", "application"]:
        tree = ast.parse(Path(f"am_backtesting/{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            assert not any(
                m.split(".")[0] in {"fastapi", "uvicorn", "argparse", "api", "cli", "service"}
                for m in modules
            )


def test_committed_contracts_unchanged():
    from am_backtesting.api import create_app
    from am_backtesting.models import Strategy

    assert create_app().openapi() == json.loads(Path("contracts/openapi.json").read_text())
    assert Strategy.model_json_schema() == json.loads(
        Path("contracts/strategy.schema.json").read_text()
    )


def test_cli_help_and_openapi():
    help_result = subprocess.run(
        [sys.executable, "-m", "am_backtesting", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert all(
        command in help_result.stdout for command in ["prepare", "prove", "unlock", "openapi"]
    )
    result = subprocess.run(
        [sys.executable, "-m", "am_backtesting", "openapi"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == json.loads(Path("contracts/openapi.json").read_text())
