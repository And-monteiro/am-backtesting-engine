import argparse
import json
import os
from pathlib import Path

from .data import prepare, unlock
from .proof import run_proof
from .util import EngineError, config_root


def main():
    parser = argparse.ArgumentParser(description="AM engine administration; no discovery loop")
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser(
        "prepare", help="Partition raw data without strategy evaluation"
    )
    preparation.add_argument("--source", type=Path, default=Path("prices"))
    preparation.add_argument("--destination", type=Path, default=Path("data"))
    commands.add_parser("prove", help="Run synthetic proof, print rules/results, and stop")
    unlocking = commands.add_parser(
        "unlock", help="Explicitly expose a sealed track; irrevocably spends holdout"
    )
    unlocking.add_argument("--sealed", type=Path, default=Path("data/sealed"))
    unlocking.add_argument("--destination", type=Path, default=Path("data/unlocked"))
    unlocking.add_argument("--track", choices=["modern", "xmr"], required=True)
    unlocking.add_argument("--acknowledge-unseen-data-is-spent", action="store_true")
    commands.add_parser("openapi", help="Print OpenAPI contract")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(json.dumps(prepare(args.source, args.destination), indent=2))
        elif args.command == "unlock":
            unlock(args.sealed, args.destination, args.track, args.acknowledge_unseen_data_is_spent)
            print(json.dumps({"track": args.track, "unlocked": True, "unseen_data_spent": True}))
        elif args.command == "prove":
            config = config_root()
            print((config / "rules.md").read_text(encoding="utf-8"))
            report = run_proof(
                config, Path(os.environ.get("AM_STATE_ROOT", "state")) / "proof.json"
            )
            print(json.dumps(report, indent=2))
            if not report["passed"]:
                raise SystemExit(1)
        else:
            from .api import app

            print(json.dumps(app.openapi(), indent=2))
    except EngineError as error:
        print(json.dumps({"code": error.code, "message": error.message}))
        raise SystemExit(1) from error
