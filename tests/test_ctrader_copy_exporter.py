import asyncio
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tools.ctrader_copy_exporter.exporter import DryRunExporter, ScrollState
from tools.ctrader_copy_exporter.schedule import (
    daily_intervals,
    require_single_week,
    weekly_intervals,
)
from tools.ctrader_copy_exporter.store import ExportStore

TZ = ZoneInfo("Europe/London")


class FakeHistoryAdapter:
    def __init__(self, rows: list[dict[str, str]], *, filter_ok: bool = True) -> None:
        self.all_rows = rows
        self.rows = rows
        self.filter_ok = filter_ok
        self.filter_applied = False
        self.filter = None
        self.top = 0
        self.height = 2
        self.reset_points: set[int] = set()
        self.revision = 0
        self.reset_pending = False
        self.events: list[str] = []

    async def apply_date_filter(self, interval):
        self.events.append("filter")
        self.filter_applied = True
        self.filter = interval
        self.rows = [
            row
            for row in self.all_rows
            if interval.ui_start
            <= datetime.fromisoformat(row["timestamp"]).astimezone(TZ).date().isoformat()
            <= interval.ui_end
        ]
        return {"start": interval.ui_start, "end": interval.ui_end}

    async def confirm_date_filter(self, interval):
        return self.filter_ok and self.filter == interval

    async def visible_rows(self):
        assert self.filter_applied, "date filter must be applied before reading rows"
        start = self.top
        return self.rows[start : start + self.height]

    async def scroll_state(self):
        if self.reset_pending:
            self.top = 0
            self.revision += 1
            self.reset_pending = False
        return ScrollState(self.top, self.height, len(self.rows), self.revision)

    async def scroll_down(self):
        next_top = min(self.top + self.height, max(0, len(self.rows) - self.height))
        if next_top in self.reset_points:
            self.reset_points.remove(next_top)
            self.reset_pending = True
        self.top = next_top

    async def scroll_top(self):
        self.top = 0

    async def wait_settled(self):
        return True


def _week():
    return require_single_week(
        datetime(2026, 10, 12, tzinfo=TZ),
        datetime(2026, 10, 19, tzinfo=TZ),
        TZ,
    )


def _rows(count: int, start: datetime | None = None) -> list[dict[str, str]]:
    start = start or datetime(2026, 10, 12, tzinfo=TZ)
    return [
        {
            "trade_id": f"trade-{index:03}",
            "timestamp": (start + timedelta(hours=index)).isoformat(),
            "symbol": "WTI",
        }
        for index in range(count)
    ]


def test_weekly_schedule_clips_ranges_and_handles_dst_boundary() -> None:
    london = ZoneInfo("Europe/London")
    intervals = weekly_intervals(
        datetime(2026, 3, 22, 10, tzinfo=london),
        datetime(2026, 3, 30, 12, tzinfo=london),
        london,
    )

    assert len(intervals) == 3
    assert intervals[0].start.isoformat() == "2026-03-22T10:00:00+00:00"
    assert intervals[0].end.isoformat() == "2026-03-23T00:00:00+00:00"
    assert intervals[1].start.isoformat() == "2026-03-23T00:00:00+00:00"
    assert intervals[1].end.isoformat() == "2026-03-30T00:00:00+01:00"
    assert (
        intervals[1].end.astimezone(UTC).timestamp()
        - intervals[1].start.astimezone(UTC).timestamp()
        == 167 * 60 * 60
    )
    assert intervals[2].start.isoformat() == "2026-03-30T00:00:00+01:00"
    assert intervals[2].end.isoformat() == "2026-03-30T12:00:00+01:00"
    assert intervals[1].ui_end == intervals[2].ui_start
    assert (
        require_single_week(
            datetime(2026, 10, 12, tzinfo=london),
            datetime(2026, 10, 19, tzinfo=london),
            london,
        ).granularity
        == "week"
    )
    with pytest.raises(ValueError, match="exactly one"):
        require_single_week(
            datetime(2026, 10, 12, tzinfo=london),
            datetime(2026, 10, 20, tzinfo=london),
            london,
        )


def test_daily_fallback_is_local_and_clipped_to_parent_week() -> None:
    parent = require_single_week(
        datetime(2026, 10, 14, 15, tzinfo=TZ),
        datetime(2026, 10, 17, 9, tzinfo=TZ),
        TZ,
    )
    days = daily_intervals(parent, TZ)

    assert [day.start.astimezone(TZ).date().isoformat() for day in days] == [
        "2026-10-14",
        "2026-10-15",
        "2026-10-16",
        "2026-10-17",
    ]
    assert days[0].start == parent.start
    assert days[-1].end == parent.end
    assert all(day.parent_id == parent.interval_id for day in days)


def test_ui_date_overlap_is_clipped_to_exact_half_open_bounds(tmp_path: Path) -> None:
    start = datetime(2026, 10, 12, 12, tzinfo=TZ)
    end = datetime(2026, 10, 13, tzinfo=TZ)
    interval = require_single_week(start, end, TZ)
    rows = [
        {"trade_id": "before", "timestamp": datetime(2026, 10, 12, 11, tzinfo=TZ).isoformat()},
        {"trade_id": "inside", "timestamp": start.isoformat()},
        {"trade_id": "at-end", "timestamp": end.isoformat()},
    ]
    adapter = FakeHistoryAdapter(rows)
    store = ExportStore(tmp_path, "range-clip")
    try:
        summary = asyncio.run(DryRunExporter(adapter, store, TZ).run_week(interval))
        assert summary["unique_trades"] == 1
        assert store.trade_ids() == {"inside"}
        assert summary["ui_filter"] == {"start": "2026-10-12", "end": "2026-10-13"}
    finally:
        store.close()


def test_successful_dry_run_persists_csv_and_verifies_bottom(tmp_path: Path) -> None:
    adapter = FakeHistoryAdapter(_rows(5))
    store = ExportStore(tmp_path, "dry-run-test")
    try:
        summary = asyncio.run(DryRunExporter(adapter, store, TZ).run_week(_week()))

        assert summary["unique_trades"] == 5
        assert summary["completed_intervals"] == 1
        assert summary["incomplete_intervals"] == 0
        assert summary["interval_results"][0]["verified"]
        assert adapter.events == ["filter"]
        with store.csv_path.open(encoding="utf-8", newline="") as source:
            csv_rows = list(csv.DictReader(source))
        assert len(csv_rows) == 5
        assert len({row["trade_id"] for row in csv_rows}) == 5
        assert store.checkpoint_status(_week().interval_id) == "completed"
        assert store.batch_count() >= 1
    finally:
        store.close()


def test_dry_run_never_persists_more_than_twenty_or_marks_complete(tmp_path: Path) -> None:
    adapter = FakeHistoryAdapter(_rows(21))
    store = ExportStore(tmp_path, "dry-run-limit")
    try:
        summary = asyncio.run(DryRunExporter(adapter, store, TZ).run_week(_week()))
        assert summary["unique_trades"] == 20
        assert summary["interval_results"][0]["reason"] == "dry_run_limit_reached"
        assert summary["incomplete_intervals"] == 1
        assert store.checkpoint_status(_week().interval_id) == "incomplete"
    finally:
        store.close()


def test_exactly_twenty_trades_can_complete_verification(tmp_path: Path) -> None:
    store = ExportStore(tmp_path, "exactly-twenty")
    try:
        summary = asyncio.run(
            DryRunExporter(FakeHistoryAdapter(_rows(20)), store, TZ).run_week(_week())
        )
        assert summary["unique_trades"] == 20
        assert summary["completed_intervals"] == 1
        assert summary["interval_results"][0]["verified"]
    finally:
        store.close()


def test_weekly_refresh_resets_trigger_daily_fallback(tmp_path: Path) -> None:
    rows = _rows(16)
    adapter = FakeHistoryAdapter(rows)
    adapter.reset_points = {4, 8, 12}
    store = ExportStore(tmp_path, "dry-run-fallback")
    try:
        summary = asyncio.run(DryRunExporter(adapter, store, TZ).run_week(_week()))
        assert summary["daily_fallback"]
        assert summary["daily_intervals"] == 7
        assert store.checkpoint_status(_week().interval_id) == "completed"
        assert summary["unique_trades"] == 16
        assert summary["interval_results"][0]["status"] == "completed"
    finally:
        store.close()


def test_configured_trade_threshold_triggers_daily_fallback(tmp_path: Path) -> None:
    store = ExportStore(tmp_path, "threshold-fallback")
    try:
        summary = asyncio.run(
            DryRunExporter(
                FakeHistoryAdapter(_rows(4)),
                store,
                TZ,
                weekly_trade_threshold=2,
            ).run_week(_week())
        )
        assert summary["daily_fallback"]
        assert summary["daily_intervals"] == 7
        assert store.checkpoint_status(_week().interval_id) == "completed"
    finally:
        store.close()


def test_filter_failure_missing_ids_and_unverified_bottom_are_incomplete(tmp_path: Path) -> None:
    interval = _week()
    failed_filter = FakeHistoryAdapter(_rows(1), filter_ok=False)
    store = ExportStore(tmp_path / "filter", "filter-failure")
    try:
        summary = asyncio.run(DryRunExporter(failed_filter, store, TZ).run_week(interval))
        assert summary["interval_results"][0]["reason"] == "filter_not_confirmed"
    finally:
        store.close()

    missing_id = _rows(1)
    missing_id[0]["trade_id"] = ""
    store = ExportStore(tmp_path / "missing", "missing-id")
    try:
        summary = asyncio.run(
            DryRunExporter(FakeHistoryAdapter(missing_id), store, TZ).run_week(interval)
        )
        assert summary["interval_results"][0]["reason"] == "missing_trade_id"
        assert summary["unique_trades"] == 0
    finally:
        store.close()

    store = ExportStore(tmp_path / "no-bottom", "no-bottom")
    try:

        class NoBottomAdapter(FakeHistoryAdapter):
            async def scroll_state(self):
                return ScrollState(self.top, self.height, len(self.rows) + 100, self.revision)

        summary = asyncio.run(
            DryRunExporter(NoBottomAdapter(_rows(3)), store, TZ, max_scroll_steps=5).run_week(
                interval
            )
        )
        assert summary["interval_results"][0]["reason"] == "bottom_not_reached"
        assert summary["incomplete_intervals"] == 1
    finally:
        store.close()


def test_conflicting_trade_id_timestamps_leave_interval_incomplete(tmp_path: Path) -> None:
    rows = _rows(2)
    rows[1]["trade_id"] = rows[0]["trade_id"]
    store = ExportStore(tmp_path, "identity-conflict")
    try:
        summary = asyncio.run(DryRunExporter(FakeHistoryAdapter(rows), store, TZ).run_week(_week()))
        assert summary["interval_results"][0]["reason"] == "trade_identity_conflict"
        assert summary["incomplete_intervals"] == 1
        assert store.unique_count() == 0
    finally:
        store.close()


def test_ambiguous_local_dst_timestamp_is_rejected(tmp_path: Path) -> None:
    autumn_week = require_single_week(
        datetime(2026, 10, 19, tzinfo=TZ),
        datetime(2026, 10, 26, tzinfo=TZ),
        TZ,
    )
    rows = [{"trade_id": "ambiguous", "timestamp": "2026-10-25T01:30:00"}]
    store = ExportStore(tmp_path, "ambiguous-time")
    try:
        summary = asyncio.run(
            DryRunExporter(FakeHistoryAdapter(rows), store, TZ).run_week(autumn_week)
        )
        assert summary["interval_results"][0]["reason"] == "ambiguous_local_timestamp"
        assert summary["incomplete_intervals"] == 1
        assert store.unique_count() == 0
    finally:
        store.close()


def test_refresh_replay_deduplicates_trade_ids(tmp_path: Path) -> None:
    adapter = FakeHistoryAdapter(_rows(5))
    adapter.reset_points = {3}
    store = ExportStore(tmp_path, "refresh-dedup")
    try:
        summary = asyncio.run(DryRunExporter(adapter, store, TZ).run_week(_week()))
        assert summary["unique_trades"] == 5
        assert summary["refresh_events"] >= 1
        assert len(store.trade_ids()) == 5
    finally:
        store.close()


def test_verification_detects_missing_previously_saved_visible_row(tmp_path: Path) -> None:
    class VanishingRowAdapter(FakeHistoryAdapter):
        def __init__(self, rows):
            super().__init__(rows)
            self.top_resets = 0

        async def scroll_top(self):
            self.top = 0
            self.top_resets += 1
            if self.top_resets >= 2:
                self.rows = self.rows[:-1]

    store = ExportStore(tmp_path, "verification-missing")
    try:
        summary = asyncio.run(
            DryRunExporter(VanishingRowAdapter(_rows(5)), store, TZ).run_week(_week())
        )
        assert summary["unique_trades"] == 5
        assert summary["interval_results"][0]["reason"] == "verification_found_missing"
        assert summary["incomplete_intervals"] == 1
        assert store.checkpoint_status(_week().interval_id) == "incomplete"
    finally:
        store.close()


def test_incremental_store_rebuilds_csv_and_deduplicates_across_intervals(tmp_path: Path) -> None:
    store = ExportStore(tmp_path, "recovery")
    week = _week()
    trade = {
        "trade_id": "same-id",
        "opened_at_utc": week.start.astimezone(UTC).isoformat(),
        "displayed_timestamp": week.start.isoformat(),
        "ui_timezone": str(TZ),
        "raw_fields": {"trade_id": "same-id", "timestamp": week.start.isoformat()},
    }
    try:
        assert store.upsert_batch(week, [trade]) == (1, 0)
        day = daily_intervals(week, TZ)[0]
        assert store.upsert_batch(day, [trade]) == (0, 1)
        assert store.unique_count() == 1
        assert store.batch_count() == 2
        store.close()

        resumed = ExportStore(tmp_path, "recovery")
        try:
            with resumed.csv_path.open(encoding="utf-8", newline="") as source:
                assert len(list(csv.DictReader(source))) == 1
            assert resumed.contains_trade("same-id")
        finally:
            resumed.close()
    except Exception:
        store.close()
        raise
