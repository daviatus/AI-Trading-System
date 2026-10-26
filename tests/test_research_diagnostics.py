"""Artifact diagnostics must not fabricate missing evidence or mutate holdout inputs."""

import builtins
import copy
import json
import runpy
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trading.research import diagnostics as d
from trading.research import safety


@pytest.fixture
def config():
    return {
        "output": "data/research/phase5-v1",
        "assets": ["EURUSD", "XAUUSD", "WTI"],
        "free_floor_gib": 15,
        "minimum_available_ram_gib": 1.5,
        "threads": 1,
        "initial_equity": 10000,
        "horizons": [15],
        "models": [{}],
        "cost_multipliers": [1.0, 2.0, 3.0],
        "folds": [
            {
                "name": "holdout-2026",
                "train_end": "2025-07-01",
                "calibration_end": "2025-10-01",
                "tuning_end": "2026-01-01",
                "test_end": "2026-10-22",
                "holdout": True,
            }
        ],
    }


@pytest.fixture
def isolated(tmp_path, monkeypatch, config):
    # Synthetic artifacts only; no production resources or scientific imports needed.
    monkeypatch.setattr(d, "ROOT", tmp_path)
    monkeypatch.setattr(d, "CODE_FILES", ())
    monkeypatch.setattr(d, "resources", lambda _: None)
    source = tmp_path / config["output"]
    source.mkdir(parents=True)
    (source / "config.json").write_text(d.encoded(config), encoding="utf-8")
    frozen = source / "evaluation/holdout-2026-frozen.json"
    frozen.parent.mkdir()
    choices = {
        a: {"selected": {"active": False}, "xgb_baseline": {}, "candidates": []}
        for a in config["assets"]
    }
    import hashlib

    frozen.write_text(
        d.encoded(
            {
                "holdout": True,
                "choices": choices,
                "choice_hash": hashlib.sha256(
                    json.dumps(choices, sort_keys=True).encode()
                ).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    for a, choice in choices.items():
        path = source / f"choices/holdout-2026/{a}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(d.encoded(choice), encoding="utf-8")
    return source, tmp_path / "data/research/phase5b-v1"


def trade(pnl, minute=0, asset="EURUSD"):
    return {
        "asset": asset,
        "net_pnl": pnl,
        "cost": 1.0,
        "entry_time": datetime(2026, 1, 5, 12, minute, tzinfo=UTC),
        "exit_time": datetime(2026, 1, 5, 12, minute + 1, tzinfo=UTC),
    }


def batch(rows):
    return SimpleNamespace(to_pylist=lambda: rows)


def test_metric_aggregation_and_zero_trade_assets(config, monkeypatch):
    monkeypatch.setattr(
        d,
        "batches",
        lambda *a, **k: iter([batch([trade(100), trade(-50, 2)]), batch([trade(0, 4)])]),
    )
    totals, checks = d.summarize_trades(None, config["assets"], 10000, config["folds"][0], config)
    m = totals["EURUSD"]
    assert m["trades"] == 3
    assert m["profit_factor"] == 2
    assert m["net_return_contribution"] == 0.005
    assert m["realized_attribution_drawdown"] == pytest.approx(50 / 10100)
    assert m["active_entry_days"] == 1
    assert m["maximum_mark_to_market_drawdown"] is None
    assert totals["WTI"]["trades"] == 0
    assert totals["WTI"]["profit_factor"] is None
    assert not any(checks.values())


def test_all_wins_profit_factor_is_undefined():
    totals = d.TradeTotals(10000)
    totals.add(trade(10))
    assert totals.result()["profit_factor"] is None
    assert totals.result()["unresolved_terminal_positions"] is None
    assert "unresolved_terminal_mark" in totals.result()["missing_trade_fields"]
    with pytest.raises(ValueError, match="Nonfinite"):
        totals.add(trade(float("nan")))


def candidate(n=30, unresolved=0, net=-0.02, dd=0.01):
    return {
        "performance": {
            "trades": n,
            "unresolved_terminal_positions": unresolved,
            "net_return": net,
            "maximum_mark_to_market_drawdown": dd,
        },
        "selection_score": -1e6 if n < 30 or unresolved else net - 0.5 * dd,
    }


def test_exclusive_and_marginal_gate_attribution():
    result = d.gate_attribution(
        {
            "selected": {"active": False},
            "candidates": [
                candidate(0, 1),
                candidate(40, 1),
                candidate(),
                candidate(31, net=0, dd=0),
            ],
        }
    )
    assert result["candidate_count"] == 4
    assert result["exclusive_reasons_in_order"] == {
        "below_30_trades": 1,
        "unresolved_exit": 1,
        "nonpositive_score": 2,
        "positive_eligible": 0,
        "missing_provenance": 0,
    }
    assert result["marginal_reasons"]["unresolved_exit"] == 2
    assert result["activation_consistent_with_candidates"]
    assert result["score_reconstruction_mismatches"] == 0
    assert result["evaluation_behavior"]["status"] == "inferred"
    for gate in result["historical_signal_and_execution_rejection_counts"].values():
        assert gate["status"] == "unavailable"
        assert gate["value"] is None


def test_missing_gate_fields_and_inconsistent_active_flag():
    missing = d.gate_attribution({"selected": {}, "candidates": [{}]})
    assert missing["exclusive_reasons_in_order"]["missing_provenance"] == 1
    assert missing["activation_consistent_with_candidates"] is None
    assert d.gate_attribution(None)["status"] == "unavailable"
    inconsistent = d.gate_attribution(
        {"selected": {"active": False}, "candidates": [candidate(net=0.1)]}
    )
    assert inconsistent["activation_consistent_with_candidates"] is False


def test_recorded_model_boundary_purging(config):
    f = config["folds"][0]
    training = {
        "train_last": "2025-06-30T23:44:00Z",
        "calibration_first": "2025-07-01T00:00:00Z",
        "calibration_last": "2025-09-30T23:44:00Z",
    }
    assert all(d.audit_training(training, f, 15)["value"].values())
    training["train_last"] = "2025-06-30T23:45:00Z"
    assert (
        d.audit_training(training, f, 15)["value"]["train_label_end_before_train_boundary"] is False
    )
    assert d.audit_training({}, f, 15)["status"] == "unavailable"


def test_trade_chronological_integrity(config):
    row = trade(1)
    assert not any(d.chronological_trade_errors(row, None, config["folds"][0]).values())
    assert (
        d.chronological_trade_errors(row, row["exit_time"], config["folds"][0])[
            "overlapping_shared_positions"
        ]
        == 1
    )
    row["exit_time"] = row["entry_time"] - timedelta(minutes=1)
    assert d.chronological_trade_errors(row, None, config["folds"][0])["outside_window"] == 1


def test_equity_metrics_reconcile_without_rewriting(config, monkeypatch):
    rows = [
        {"equity": v, "time": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i)}
        for i, v in enumerate([10000, 10100, 10050])
    ]
    monkeypatch.setattr(d, "batches", lambda *a, **k: iter([batch(rows)]))
    result = d.audit_equity(
        None, {"net_return": 0.005, "maximum_mark_to_market_drawdown": 50 / 10100}, config
    )
    assert result["drawdown_matches_result"]
    assert result["net_return_matches_result"]
    assert result["mark_rows"] == 3


def test_probabilities_are_conditional_not_full_stream(config, monkeypatch):
    rows = [{"asset": "EURUSD", "probability": p, "direction": 1} for p in [0.45, 0.6, 1.0]]
    monkeypatch.setattr(d, "batches", lambda *a, **k: iter([batch(rows)]))
    m = d.summarize_opportunities(None, config["assets"], config, {"EURUSD": 100})
    assert m["EURUSD"]["accepted_opportunities_per_complete_feature_row"] == 0.03
    assert sum(m["EURUSD"]["histogram"]) == 3
    assert "CONDITIONAL" in m["EURUSD"]["distribution_scope"]
    assert m["WTI"]["probability_mean"] is None
    assert m["WTI"]["complete_feature_rows"] is None


def test_missing_artifacts_are_explicit(config, isolated):
    source, output = isolated
    report = d.run(config, source, output)
    assert "results/holdout-2026.json" in report["missing_artifacts"]
    assert report["windows"][0]["portfolio"]["xgb_baseline:cost1"]["value"] is None
    assert report["windows"][0]["by_asset"]["xgb_baseline:cost1"]["value"] is None
    assert report["alignment"]["WTI"]["status"] == "unavailable"
    assert "unavailable" in (output / "diagnostics.md").read_text(encoding="utf-8")


def test_holdout_and_config_immutability_and_idempotence(config, isolated):
    source, output = isolated
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in source.rglob("*") if p.is_file()}
    d.run(config, source, output)
    first = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()}
    d.run(config, source, output)
    assert first == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in output.iterdir()}
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}
    changed = copy.deepcopy(config)
    changed["horizons"] = [60]
    with pytest.raises(ValueError, match="Frozen experiment config"):
        d.run(changed, source, output)
    with pytest.raises(ValueError, match="separate"):
        d.run(config, source, source / "diagnostics")
    with pytest.raises(ValueError, match="separate"):
        d.run(config, source, source)
    with pytest.raises(ValueError, match="stay under"):
        d.run(config, source, source.parents[2] / "outside")


def test_changed_evidence_requires_new_version(config, isolated):
    source, output = isolated
    d.run(config, source, output)
    (source / "environment.json").write_text('{"fixture": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="version differs"):
        d.run(config, source, output)


def test_inflight_evidence_mutation_publishes_nothing(config, isolated, monkeypatch):
    source, output = isolated
    original = d.build

    def change(*args):
        report = original(*args)
        (source / "environment.json").write_text("{}", encoding="utf-8")
        return report

    monkeypatch.setattr(d, "build", change)
    with pytest.raises(ValueError, match="changed during"):
        d.run(config, source, output)
    assert not output.exists()


def test_config_chronology_rejected(config, isolated):
    source, output = isolated
    config["folds"][0]["tuning_end"] = "2025-01-01"
    (source / "config.json").write_text(d.encoded(config), encoding="utf-8")
    with pytest.raises(ValueError, match="Chronological"):
        d.run(config, source, output)


def test_overlapping_tests_and_development_crossing_holdout(config):
    overlapping = copy.deepcopy(config)
    overlapping["folds"].append(copy.deepcopy(overlapping["folds"][0]))
    with pytest.raises(ValueError, match="overlap"):
        d.chronological_protocol(overlapping)
    crossing = copy.deepcopy(config)
    development = {
        "name": "invalid-dev",
        "train_end": "2026-10-23",
        "calibration_end": "2026-11-01",
        "tuning_end": "2026-12-01",
        "test_end": "2027-01-01",
        "holdout": False,
    }
    crossing["folds"].append(development)
    with pytest.raises(ValueError, match="crosses final holdout"):
        d.chronological_protocol(crossing)


def test_cli_dispatch_avoids_training_and_database_imports(monkeypatch):
    original_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name.split(".")[0] not in {"xgboost", "sqlalchemy", "scipy", "pandas", "numpy"}
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(safety, "bootstrap_gate", lambda _: None)
    monkeypatch.setattr(sys, "argv", ["research", "diagnostics"])
    monkeypatch.setattr(d, "cli", lambda _: 0)
    with pytest.raises(SystemExit) as stopped:
        runpy.run_module("trading.research.runner", run_name="__main__")
    assert stopped.value.code == 0


def test_safety_stop_publishes_nothing(config, isolated, monkeypatch):
    source, output = isolated

    def stop(_):
        raise safety.ResearchSafetyStop("fixture low RAM")

    monkeypatch.setattr(d, "resources", stop)
    with pytest.raises(safety.ResearchSafetyStop):
        d.run(config, source, output)
    assert not output.exists()


def test_real_parquet_alignment_and_reset_audit(config, isolated):
    # Real IO check respects host floors; no mocked gate is used for scientific imports/reads.
    try:
        safety.resources(config)
    except safety.ResearchSafetyStop as error:
        pytest.skip(str(error))
    import pyarrow as pa
    import pyarrow.parquet as pq

    source, _ = isolated
    stamps = [datetime(2026, 1, 5, tzinfo=UTC) + timedelta(minutes=i) for i in range(70)]
    stamps += [stamps[-1] + timedelta(minutes=2)]
    for kind in ("bars", "features"):
        (source / kind).mkdir()
    pq.write_table(
        pa.table({"opened_at": pa.array(stamps, type=pa.timestamp("us", tz="UTC"))}),
        source / "bars/EURUSD.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "opened_at": pa.array(stamps, type=pa.timestamp("ns", tz="UTC")),
                "f": [float("nan")] * 63 + [1.0] * 8,
            }
        ),
        source / "features/EURUSD.parquet",
    )
    # Restore the real safety gate for streamed reads too.
    original = d.resources
    d.resources = safety.resources
    try:
        result = d.scan_alignment(d.Evidence(source), "EURUSD", config)["value"]
    except safety.ResearchSafetyStop as error:
        pytest.skip(str(error))
    finally:
        d.resources = original
    assert result["checks"]["misaligned_rows"] == 0
    assert result["checks"]["complete_features_during_63_bar_warmup"] == 1
    assert result["windows"]["holdout-2026"]["complete_feature_rows"] == 8
