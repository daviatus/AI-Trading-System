"""PostgreSQL-only supervisors must not preload analytical export libraries."""

import subprocess
import sys
from pathlib import Path


def test_collector_import_and_empty_export_need_no_analytics_libraries():
    # A clean interpreter catches transitive imports hidden by pytest's module cache.
    # Import modules only: never call a collector CLI, connect or start a supervisor.
    script = """
import importlib.abc
import sys
from datetime import UTC, datetime
from pathlib import Path

class NoAnalytics(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'pyarrow', 'numpy', 'pandas'}:
            raise AssertionError('Unnecessary analytics import: ' + fullname)

sys.meta_path.insert(0, NoAnalytics())
import trading.ctrader.multiasset
from trading.ctrader.parquet import persist_candles
assert 'trading.ctrader.history_backfill' in sys.modules
assert 'trading.ctrader.worker' in sys.modules
assert 'trading.ctrader.sync' in sys.modules
assert 'trading.data.parquet_store' not in sys.modules
stamp = datetime(2026, 1, 1, tzinfo=UTC)
assert persist_candles([], Path('unused-empty-output'), stamp, stamp) == []
assert not any(name.split('.')[0] in {'pyarrow', 'numpy', 'pandas'} for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr
