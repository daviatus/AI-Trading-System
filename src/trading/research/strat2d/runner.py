"""STRAT-2D offline-only CLI with strict resource bootstrap and safe failure evidence."""

import argparse
import json
import uuid

from trading.research.safety import ROOT, ResearchSafetyStop
from trading.research.strat2d.journal import write_new
from trading.research.strat2d.pipeline import Monitor, run, validate_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/strat2d-eurusd-2022-engineering-v1.json")
    parser.add_argument("--max-transactions", type=int, default=1)
    args = parser.parse_args(argv)
    config = json.loads((ROOT / args.config).read_text(encoding="utf-8-sig"))
    _, output = validate_config(config)
    if args.max_transactions < 1:
        raise ValueError("Positive transaction budget required")
    monitor = Monitor(config)
    try:
        state = run(config, max_transactions=args.max_transactions, guard=monitor)
    except Exception as error:
        # Never print raw error messages, config contents, traceback locals or credentials.
        from trading.research.phase6.runner import _failure_record

        report = _failure_record(error, {"stage": "strat2d_pipeline"})
        path = output / f"failure-{uuid.uuid4().hex}.json"
        output.mkdir(parents=True, exist_ok=True)
        write_new(path, report)
        print(f"STRAT2D_STOPPED {type(error).__name__}; evidence={path.relative_to(ROOT)}")
        return 2 if isinstance(error, ResearchSafetyStop) else 1
    print(
        json.dumps(
            {
                "complete": state["complete"],
                "phase": state["phase"],
                "status": state["status"],
                "train_rows": state["model"]["train_rows"],
                "calibration_rows": state["model"]["calibration_rows"],
                "metrics": vars(monitor),
            },
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
