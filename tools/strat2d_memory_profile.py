"""Fresh-process RSS measurements; no services or user processes are manipulated."""

import argparse
import ctypes
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading.research import safety
from trading.research.phase6 import controller

ROOT = safety.ROOT
AUDIT = ROOT / ".tmp/strat2d-memory-v1"


class Sampler:
    """Cached Windows probes: profiling itself does not create per-sample ctypes types."""

    def __init__(self):
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "peak_rss",
                    "rss",
                    "peak_paged",
                    "paged",
                    "peak_nonpaged",
                    "nonpaged",
                    "commit",
                    "peak_commit",
                )
            ]

        self.Counters = Counters
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.psapi = ctypes.WinDLL("psapi", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = wintypes.HANDLE
        self.psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        self.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    def measure(self):
        value = self.Counters()
        value.cb = ctypes.sizeof(value)
        if not self.psapi.GetProcessMemoryInfo(
            self.kernel.GetCurrentProcess(), ctypes.byref(value), value.cb
        ):
            raise RuntimeError("Profiling memory measurement unavailable")
        return {
            "rss_bytes": int(value.rss),
            "peak_rss_bytes": int(value.peak_rss),
            "private_commit_bytes": int(value.commit),
            "available_ram_bytes": safety.available_memory(),
            "ctypes_pointer_cache_entries": len(getattr(ctypes, "_pointer_type_cache", {})),
        }


def build_fixture():
    # Preparation only, under the already approved preparation policy. No fitting.
    policy = {
        "preparation_only": True,
        "mode": "offline_simulation",
        "threads": 1,
        "free_floor_gib": 15,
        "minimum_available_ram_gib": 1.25,
    }
    safety.resources(policy)
    import numpy as np
    import pandas as pd

    from trading.research.phase6.labels import LabelSpec
    from trading.research.phase6.streaming import prepare_stream

    safety.resources(policy)
    index = pd.date_range("2024-01-08", periods=6144, freq="min", tz="UTC", name="opened_at")
    price = 1.1 + 0.001 * np.sin(np.arange(len(index)) / 13)
    frame = pd.DataFrame(
        {
            "open": price,
            "high": price + 0.0002,
            "low": price - 0.0002,
            "close": price,
            "volume": 1.0,
        },
        index=index,
    )
    raw = AUDIT / "raw/cache/EURUSD/2024-01.parquet"
    raw.parent.mkdir(parents=True, exist_ok=True)
    frame.reset_index().to_parquet(raw, index=False)
    (AUDIT / "raw/snapshot.json").write_text(
        json.dumps({"parts": {"cache/EURUSD/2024-01.parquet": {"sha256": controller.digest(raw)}}})
    )
    metadata = {
        "specifications": {
            "scheduleTimeZone": "UTC",
            "schedule": [{"startSecond": 0, "endSecond": 604800}],
            "holiday": [],
        }
    }
    prepared = ROOT / "data/research/strat2d-memory-synthetic-prepared-v1"
    require_new(prepared)
    prepare_stream(
        AUDIT / "raw",
        prepared,
        "EURUSD",
        index[0].isoformat(),
        (index[-1] + pd.Timedelta(minutes=1)).isoformat(),
        metadata=metadata,
        label_spec=LabelSpec(kind="barrier", horizon_minutes=15, reward_risk=3, cost_bps=2.1),
        batch_rows=512,
        guard=lambda: safety.resources(policy),
    )
    config = json.loads((ROOT / "config/strat2d-eurusd-2022-engineering-v1.json").read_text())
    from trading.research.strat2d.journal import logical_hash

    config.update(
        start=index[0].isoformat(),
        train_end=index[2048].isoformat(),
        calibration_end=index[4096].isoformat(),
        test_end=(index[-1] + pd.Timedelta(minutes=1)).isoformat(),
        prepared=prepared.relative_to(ROOT).as_posix(),
    )
    config["calendar"].update(
        timezone="UTC",
        weekly_open_seconds=[[0, 604800]],
        verified=True,
        evidence_fingerprint=logical_hash(metadata),
    )
    with (AUDIT / "synthetic-config.json").open("x", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)


def require_new(path):
    if Path(path).exists():
        raise ValueError("Profiling output already exists")


def profile(args):
    for name in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
    ):
        os.environ[name] = "1"
    if args.condition == "baseline":
        import trading.research

        # Pin shared bootstrap modules above, then use the preserved model/pipeline
        # package. No existing journal or report is migrated or overwritten.
        trading.research.__path__.insert(0, str(AUDIT / "baseline/src/trading/research"))
    mode_name = "historical" if args.historical else args.mode
    if args.rep is None:
        for candidate in range(1, 10000):
            stem = AUDIT / f"{args.condition}-{mode_name}-{candidate}"
            if (
                not stem.with_suffix(".json").exists()
                and not Path(str(stem) + "-crash.json").exists()
            ):
                args.rep = candidate
                break
    output = AUDIT / f"{args.condition}-{mode_name}-{args.rep}.json"
    require_new(output)
    sampler = Sampler()
    stages = []
    stage_maxima = {}
    started = time.perf_counter()

    def sample(name):
        measured = sampler.measure()
        stages.append({"stage": name, "elapsed_seconds": time.perf_counter() - started, **measured})
        return measured

    sample("bootstrap")
    result = {
        "condition": args.condition,
        "mode": args.mode,
        "rep": args.rep,
        "python": sys.version.split()[0],
        "status": "running",
        "stages": stages,
        "dataset_kind": "historical_engineering" if args.historical else "synthetic",
    }
    try:
        from trading.research.strat2d import pipeline

        config_path = (
            ROOT / "config/strat2d-eurusd-2022-engineering-v1.json"
            if args.historical
            else AUDIT / "synthetic-config.json"
        )
        if args.memory_config:
            config_path = ROOT / "config/strat2d-eurusd-2022-memory-validation-v1.json"
        config = json.loads(config_path.read_text())
        result["config_sha256"] = controller.digest(config_path)
        result["package_code"] = {
            p.name: controller.digest(p) for p in Path(pipeline.__file__).parent.glob("*.py")
        }
        config["output"] = (
            f"data/research/strat2d-memory-{args.condition}-{mode_name}-{args.rep}-v1"
        )
        if args.output:
            config["output"] = args.output
        sample("bootstrap_pipeline_import")
        safety.resources(config)
        monitor = pipeline.Monitor(config)

        def guard():
            monitor()
            # Record maxima only, rather than retaining samples or prediction history.
            frame = sys._getframe(1)
            phase = "checkpoint_or_source"
            for _ in range(8):
                if frame is None:
                    break
                state = frame.f_locals.get("state")
                if isinstance(state, dict) and state.get("phase") in {
                    "quality",
                    "train",
                    "calibration",
                    "test",
                    "done",
                }:
                    phase = state["phase"]
                    break
                frame = frame.f_back
            measured = sampler.measure()
            previous = stage_maxima.setdefault(
                phase,
                {
                    "samples": 0,
                    "max_rss_bytes": 0,
                    "minimum_available_ram_bytes": measured["available_ram_bytes"],
                },
            )
            previous["samples"] += 1
            previous["max_rss_bytes"] = max(previous["max_rss_bytes"], measured["rss_bytes"])
            previous["minimum_available_ram_bytes"] = min(
                previous["minimum_available_ram_bytes"], measured["available_ram_bytes"]
            )

        if args.mode == "imports":
            from trading.research.strat2d import model

            sample("numpy_model_import")
            from trading.research.strat2d import calendar, portfolio

            sample("pandas_calendar_portfolio_import")
            import pyarrow.parquet

            sample("arrow_import")
            try:
                from trading.research.strat2d import prepared

                del prepared
            except ImportError:
                from trading.research.phase6 import streaming

                del streaming
            sample("prepared_validation_import")
            guard()
            del model, calendar, portfolio, pyarrow
        elif args.mode == "model":
            from trading.research.strat2d.model import OnlineModel

            sample("model_import")
            guard()
            model = OnlineModel(config["model"])
            x = [0.0001] * 5 + [50.0, 0.0001, 0.001]
            for i in range(10000):
                if i % 128 == 0:
                    guard()
                decision = (datetime(2024, 1, 8, tzinfo=UTC) + timedelta(minutes=i)).isoformat()
                end = (datetime(2024, 1, 8, tzinfo=UTC) + timedelta(minutes=i + 1)).isoformat()
                model.fit_one(x, i % 3, decision, end, "2024-02-01T00:00:00+00:00")
            sample("synthetic_fit_10000")
            for i in range(1000):
                if i % 128 == 0:
                    guard()
                model.calibrate_one(x, i % 3)
                model.predict(x)
            result["logical_model_hash"] = pipeline.logical_hash(model.export())
            sample("calibration_inference_1000")
        elif args.mode == "portfolio":
            import pandas as pd

            from trading.research.strat2d.portfolio import Portfolio

            sample("portfolio_import")
            guard()
            portfolio = Portfolio(config["portfolio"])
            stamp = pd.Timestamp("2024-01-08T00:00:00Z")
            for i in range(3000):
                if i % 128 == 0:
                    guard()
                when = stamp + i * pd.Timedelta(minutes=1)
                events = []
                portfolio.signal(when, [0.1, 0.8, 0.1], 0.0003, "memory-fixture", True, events)
                portfolio.on_bar(
                    when, {"open": 1.1, "high": 1.10001, "low": 1.09999, "close": 1.1}, True, events
                )
            result["logical_portfolio_hash"] = pipeline.logical_hash(portfolio.state)
            result["fills"] = portfolio.state["fills"]
            sample("simulation_3000_bars")
        elif args.mode == "checkpoint":
            from trading.research.strat2d.journal import Journal, committed_files

            target = AUDIT / f"{args.condition}-checkpoint-{args.rep}"
            require_new(target)
            journal = Journal(target, {"fixture": "checkpoint-v1"}, {}, guard)
            blocks = [
                {
                    "start": f"2024-01-{i % 28 + 1:02}T00:00:00+00:00",
                    "end": f"2024-01-{i % 28 + 1:02}T00:01:00+00:00",
                    "reason": "synthetic",
                }
                for i in range(1024)
            ]
            for i in range(20):
                journal.commit(
                    {"row": i, "quality": {"blocks": blocks}, "model": [[0.1] * 3] * 9},
                    [{"event": "equity", "equity": 10000.0}] * 100,
                )
            sample("checkpoint_write_20")
            for _ in range(3):
                restored = Journal(target, {"fixture": "checkpoint-v1"}, {}, guard)
            result["logical_state_hash"] = pipeline.logical_hash(restored.state)
            result["artifact_hash"] = pipeline.logical_hash(committed_files(target))
            sample("checkpoint_restore_3")
        elif args.mode == "monitor":
            for _ in range(1000):
                guard()
            sample("resource_guard_1000")
        elif args.mode == "pipeline":

            def fault(when, generation):
                if when == "before_commit" and generation == args.crash_generation:
                    guard()
                    sample("intentional_hard_exit_before_publication")
                    with (AUDIT / f"{args.condition}-{mode_name}-{args.rep}-crash.json").open(
                        "x", encoding="utf-8"
                    ) as handle:
                        json.dump(
                            {"generation": generation, "stages": stages, "expected_exit": 86},
                            handle,
                            indent=2,
                        )
                        handle.flush()
                        os.fsync(handle.fileno())
                    os._exit(86)

            state = pipeline.run(config, guard=guard, fault=fault)
            sample("synthetic_pipeline_complete")
            result["logical_state_hash"] = pipeline.logical_hash(state)
            result["model_hash"] = pipeline.logical_hash(state["model"])
            result["portfolio_hash"] = pipeline.logical_hash(state["portfolio"])
            result["rows_fitted"] = state["model"]["train_rows"]
            result["pipeline_output"] = config["output"]
            result["model_rows"] = state["model"]["train_rows"]
            result["calibration_rows"] = state["model"]["calibration_rows"]
            result["test_metrics"] = state["metrics"]
            result["portfolio"] = state["portfolio"]
        result["monitor"] = {
            k: v for k, v in vars(monitor).items() if k not in {"config", "started"}
        }
        result["status"] = "PASS"
    except Exception as error:
        result["status"] = (
            "RESOURCE_STOP" if isinstance(error, safety.ResearchSafetyStop) else "FAIL"
        )
        from trading.research.phase6.runner import _failure_record

        result["failure"] = _failure_record(error, {"stage": "synthetic_memory_profile"})
        sample("stopped")
    finally:
        result["stage_maxima"] = stage_maxima
        result["loaded_modules"] = {
            name: name in sys.modules
            for name in ("numpy", "pandas", "pyarrow", "sqlalchemy", "scipy", "xgboost")
        }
        result["elapsed_seconds"] = time.perf_counter() - started
        with output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
        print(
            json.dumps({"status": result["status"], "output": str(output), "final": stages[-1]}),
            flush=True,
        )
    return 0 if result["status"] == "PASS" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-fixture", action="store_true")
    parser.add_argument("--condition", choices=["baseline", "optimized"], default="optimized")
    parser.add_argument(
        "--mode",
        choices=["imports", "model", "portfolio", "checkpoint", "monitor", "pipeline"],
        default="imports",
    )
    parser.add_argument("--rep", type=int)
    parser.add_argument("--historical", action="store_true")
    parser.add_argument("--crash-generation", type=int, default=-1)
    parser.add_argument("--memory-config", action="store_true")
    parser.add_argument(
        "--output", help="Fixed versioned output to resume; report versions remain unique"
    )
    args = parser.parse_args()
    if args.historical and args.mode != "pipeline":
        parser.error("Historical profiling is permitted only for the bounded pipeline mode")
    if args.memory_config and not args.historical:
        parser.error("The historical offline memory config requires --historical")
    if args.output and args.mode != "pipeline":
        parser.error("A resumable output override is only valid for pipeline profiling")
    if args.build_fixture:
        build_fixture()
        return 0
    return profile(args)


if __name__ == "__main__":
    raise SystemExit(main())
