"""Bounded imports, allocation lifetimes and canonical-format regressions."""

import ctypes
import hashlib
import json
import subprocess
import sys

import pytest

from trading.research.strat2d import runtime
from trading.research.strat2d.journal import canonical, logical_hash, write_new


def test_resource_probe_types_do_not_accumulate():
    runtime.process_memory()
    before = len(ctypes._pointer_type_cache)
    for _ in range(1000):
        value = runtime.process_memory()
        assert value["peak_rss_bytes"] >= value["rss_bytes"] > 0
    assert len(ctypes._pointer_type_cache) == before


@pytest.mark.parametrize(
    "value",
    [
        None,
        True,
        -0.0,
        [0, 1e-20, 1e20],
        {"é": '\n"', "weights": [[0.1, -0.2, 0.3]] * 9},
        {"quality": {"blocks": [{"start": "2024-01-01", "reason": "synthetic"}] * 1024}},
    ],
)
def test_streamed_serialization_retains_exact_canonical_bytes_and_hashes(tmp_path, value):
    path = tmp_path / "checkpoint.json"
    write_new(path, value)
    expected = canonical(value)
    assert path.read_bytes() == (expected + "\n").encode()
    assert logical_hash(value) == hashlib.sha256(expected.encode()).hexdigest()


def test_nonfinite_checkpoint_is_never_committed(tmp_path):
    with pytest.raises(ValueError):
        write_new(tmp_path / "partial.json", {"bad": float("nan")})
    assert not (tmp_path / "00000000").exists()


def test_prepared_verification_import_does_not_load_scientific_or_database_stack():
    code = (
        "import sys;from trading.research.strat2d import prepared;"
        "assert not(set(sys.modules)&{'numpy','pandas','pyarrow','sqlalchemy','scipy','xgboost'})"
    )
    subprocess.run([sys.executable, "-B", "-c", code], check=True, capture_output=True, text=True)


def test_streamed_json_checkpoint_round_trip(tmp_path):
    from trading.research.strat2d.journal import Journal

    value = {
        "weights": [[0.1] * 3] * 9,
        "quality": {"blocks": [{"start": str(i)} for i in range(256)]},
    }
    journal = Journal(tmp_path, {"schema": 1}, {})
    journal.commit(value, [{"event": "equity", "value": 10000.0}])
    restored = Journal(tmp_path, {"schema": 1}, {})
    assert restored.state == value
    assert json.loads((tmp_path / "00000000/receipt.json").read_text())[
        "logical_state_hash"
    ] == logical_hash(value)
