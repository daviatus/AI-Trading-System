import hashlib
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from tools.ctrader_copy_exporter.schedule import ExportInterval, daily_intervals
from tools.ctrader_copy_exporter.store import ExportStore


@dataclass(frozen=True)
class ScrollState:
    top: float
    height: float
    total: float
    revision: int = 0

    @property
    def at_bottom(self) -> bool:
        return self.top + self.height >= self.total


class HistoryAdapter(Protocol):
    async def apply_date_filter(self, interval: ExportInterval) -> dict[str, str]: ...

    async def confirm_date_filter(self, interval: ExportInterval) -> bool: ...

    async def visible_rows(self) -> list[dict[str, str]]: ...

    async def scroll_state(self) -> ScrollState: ...

    async def scroll_down(self) -> None: ...

    async def scroll_top(self) -> None: ...

    async def wait_settled(self) -> bool: ...


@dataclass(frozen=True)
class IntervalResult:
    interval_id: str
    status: str
    reason: str | None
    unique_count: int
    duplicate_count: int
    refresh_count: int
    bottom_reached: bool
    verified: bool


class DryRunExporter:
    """One-week-only exporter. It never runs an unbounded historical export."""

    def __init__(
        self,
        adapter: HistoryAdapter,
        store: ExportStore,
        timezone: ZoneInfo,
        *,
        unique_limit: int = 20,
        weekly_trade_threshold: int = 5000,
        reset_threshold: int = 3,
        max_scroll_steps: int = 10000,
        timestamp_format: str | None = None,
        batch_callback: Callable[[str, int, int], Awaitable[None]] | None = None,
    ) -> None:
        if unique_limit != 20:
            raise ValueError("The mandatory dry-run unique trade limit is fixed at 20")
        if weekly_trade_threshold <= 0 or reset_threshold <= 0 or max_scroll_steps <= 0:
            raise ValueError("Exporter thresholds must be positive")
        self.adapter = adapter
        self.store = store
        self.timezone = timezone
        self.unique_limit = unique_limit
        self.weekly_trade_threshold = weekly_trade_threshold
        self.reset_threshold = reset_threshold
        self.max_scroll_steps = max_scroll_steps
        self.timestamp_format = timestamp_format
        self.batch_callback = batch_callback
        self.ui_filters: dict[str, dict[str, str]] = {}

    async def run_week(self, interval: ExportInterval) -> dict[str, Any]:
        if interval.granularity != "week":
            raise ValueError("Dry run only accepts a weekly interval")
        initial_count = self.store.unique_count()
        if initial_count > self.unique_limit:
            raise ValueError("Existing run data exceeds the 20-trade dry-run ceiling")

        weekly_result = await self._extract(interval, verification=True)
        used_daily_fallback = (
            weekly_result.unique_count > self.weekly_trade_threshold
            or weekly_result.refresh_count >= self.reset_threshold
        )
        results = [weekly_result]
        if used_daily_fallback:
            self.store.checkpoint(
                interval,
                "in_progress",
                unique_count=weekly_result.unique_count,
                duplicate_count=weekly_result.duplicate_count,
                refresh_count=weekly_result.refresh_count,
                verification="weekly_to_daily_fallback",
            )
            children = daily_intervals(interval, self.timezone)
            for child in children:
                result = await self._extract(child, verification=True)
                results.append(result)
            children_complete = all(result.status == "completed" for result in results[1:])
            parent_status = "completed" if children_complete else "incomplete"
            parent_reason = None if children_complete else "daily_child_incomplete"
            results[0] = replace(
                weekly_result,
                status=parent_status,
                reason=parent_reason,
                verified=children_complete,
            )
            self.store.checkpoint(
                interval,
                parent_status,
                unique_count=self.store.unique_count(),
                duplicate_count=sum(result.duplicate_count for result in results),
                refresh_count=sum(result.refresh_count for result in results),
                verification=parent_reason or "all_daily_children_verified",
            )
        else:
            self.store.checkpoint(
                interval,
                weekly_result.status,
                unique_count=self.store.unique_count(),
                duplicate_count=weekly_result.duplicate_count,
                refresh_count=weekly_result.refresh_count,
                verification=weekly_result.reason or "verified",
            )

        checkpoints = self.store.checkpoints()
        summary = {
            "mode": "dry-run",
            "interval_id": interval.interval_id,
            "requested_interval_start": interval.start.isoformat(),
            "requested_interval_end": interval.end.isoformat(),
            "timezone": str(self.timezone),
            "ui_filter": {"start": interval.ui_start, "end": interval.ui_end},
            "ui_filters": self.ui_filters,
            "daily_fallback": used_daily_fallback,
            "weekly_intervals": 1,
            "daily_intervals": max(0, len(results) - 1),
            "completed_intervals": sum(result.status == "completed" for result in results),
            "incomplete_intervals": sum(result.status != "completed" for result in results),
            "unique_trades": self.store.unique_count(),
            "duplicate_ids": sum(result.duplicate_count for result in results),
            "refresh_events": sum(result.refresh_count for result in results),
            "interval_results": [result.__dict__ for result in results],
            "checkpoints": checkpoints,
            "csv_path": str(self.store.csv_path) if self.store.csv_enabled else None,
            "checkpoint_path": str(self.store.database_path),
            "warnings": [result.reason for result in results if result.reason is not None],
        }
        summary["export_fingerprint"] = hashlib.sha256(
            f"{self.store.run_id}:{self.store.unique_count()}:{summary['interval_results']}".encode()
        ).hexdigest()
        return summary

    async def _extract(self, interval: ExportInterval, *, verification: bool) -> IntervalResult:
        self.store.checkpoint(interval, "in_progress", verification="filter_pending")
        duplicate_count = 0
        refresh_count = 0
        try:
            ui_filter = await self.adapter.apply_date_filter(interval)
            self.ui_filters[interval.interval_id] = ui_filter
            self.store.checkpoint(
                interval,
                "in_progress",
                ui_filter=ui_filter,
                verification="filter_applied",
            )
            if not await self.adapter.confirm_date_filter(interval):
                return self._result(
                    interval, "incomplete", "filter_not_confirmed", duplicate_count, refresh_count
                )
            scan = await self._scan(interval, persist=True)
            if scan.reason:
                return self._result(
                    interval, "incomplete", scan.reason, duplicate_count, refresh_count, scan
                )
            if not scan.bottom_reached:
                return self._result(
                    interval,
                    "incomplete",
                    "bottom_not_reached",
                    duplicate_count,
                    refresh_count,
                    scan,
                )
            if not verification:
                return self._result(
                    interval,
                    "incomplete",
                    "verification_not_run",
                    duplicate_count,
                    refresh_count,
                    scan,
                )

            expected_ids = self.store.trade_ids_in_range(interval.start, interval.end)
            await self.adapter.scroll_top()
            if not await self.adapter.wait_settled():
                return self._result(
                    interval,
                    "incomplete",
                    "history_refresh_unstable",
                    duplicate_count,
                    refresh_count,
                    scan,
                )
            verify_scan = await self._scan(interval, persist=True, verification=True)
            combined_scan = _ScanResult(
                unique_count=scan.unique_count + verify_scan.unique_count,
                duplicate_count=scan.duplicate_count + verify_scan.duplicate_count,
                refresh_count=scan.refresh_count + verify_scan.refresh_count,
                bottom_reached=verify_scan.bottom_reached,
                new_ids=scan.new_ids | verify_scan.new_ids,
                last_scroll_state=verify_scan.last_scroll_state or scan.last_scroll_state,
            )
            if verify_scan.reason:
                return self._result(
                    interval,
                    "incomplete",
                    verify_scan.reason,
                    0,
                    0,
                    combined_scan,
                )
            if not verify_scan.bottom_reached:
                return self._result(
                    interval,
                    "incomplete",
                    "bottom_not_reached",
                    0,
                    0,
                    combined_scan,
                )
            if verify_scan.new_ids - expected_ids or expected_ids - verify_scan.seen_ids:
                return self._result(
                    interval,
                    "incomplete",
                    "verification_found_missing",
                    0,
                    0,
                    combined_scan,
                )
            if not await self.adapter.confirm_date_filter(interval):
                return self._result(
                    interval,
                    "incomplete",
                    "filter_not_confirmed",
                    0,
                    0,
                    combined_scan,
                )
            return self._result(interval, "completed", None, 0, 0, combined_scan)
        except MissingTradeIdError:
            return self._result(
                interval, "incomplete", "missing_trade_id", duplicate_count, refresh_count
            )
        except TradeTimestampError as error:
            return self._result(interval, "incomplete", str(error), duplicate_count, refresh_count)
        except TradeIdentityConflict:
            return self._result(
                interval, "incomplete", "trade_identity_conflict", duplicate_count, refresh_count
            )
        except (PersistenceError, OSError, sqlite3.Error):
            return self._result(
                interval, "incomplete", "persistence_error", duplicate_count, refresh_count
            )
        except Exception as error:
            return self._result(
                interval,
                "incomplete",
                f"extraction_error:{type(error).__name__}",
                duplicate_count,
                refresh_count,
            )

    async def _scan(
        self,
        interval: ExportInterval,
        *,
        persist: bool,
        verification: bool = False,
    ) -> "_ScanResult":
        result = _ScanResult()
        previous_state: ScrollState | None = None
        previous_ids: tuple[str, ...] = ()
        previous_window_start: int | None = None
        observed_positions: dict[str, int] = {}
        stagnant_steps = 0
        await self.adapter.scroll_top()

        for _ in range(self.max_scroll_steps):
            if not await self.adapter.wait_settled():
                result.reason = "history_refresh_unstable"
                return result
            if not await self.adapter.confirm_date_filter(interval):
                result.reason = "filter_not_confirmed"
                return result
            rows = await self.adapter.visible_rows()
            accepted: list[dict[str, Any]] = []
            visible_ids = []
            for raw in rows:
                trade = _parse_trade(
                    raw,
                    self.timezone,
                    interval,
                    timestamp_format=self.timestamp_format,
                )
                if trade is None:
                    continue
                visible_ids.append(trade["trade_id"])
                result.seen_ids.add(trade["trade_id"])
                if not self.store.contains_trade(trade["trade_id"]):
                    result.new_ids.add(trade["trade_id"])
                accepted.append(trade)
            inserted, duplicates, overflow = await self._persist_rows(
                interval,
                accepted,
                result,
                persist=persist,
            )
            result.duplicate_count += duplicates
            result.unique_count += inserted
            if overflow or (inserted > 0 and self.store.unique_count() >= self.unique_limit):
                state_at_limit = await self.adapter.scroll_state()
                if overflow or not state_at_limit.at_bottom:
                    result.reason = "dry_run_limit_reached"
                    return result

            state = await self.adapter.scroll_state()
            current_ids = tuple(visible_ids)
            for trade_id in current_ids:
                if trade_id not in observed_positions:
                    observed_positions[trade_id] = len(observed_positions)
            window_start = observed_positions.get(current_ids[0]) if current_ids else None
            if previous_state and previous_state.top > 1 and state.top < previous_state.top - 1:
                result.refresh_count += 1
                result.last_scroll_state = {
                    "reset_detected": True,
                    "evidence": "scroll_position_returned_toward_top",
                    "from_top": previous_state.top,
                    "to_top": state.top,
                    "visible_ids_before": list(previous_ids),
                    "visible_ids_after": list(current_ids),
                    "revision": state.revision,
                }
                if result.refresh_count >= self.reset_threshold and interval.granularity == "day":
                    result.reason = "history_refresh_unstable"
                    return result
            elif (
                previous_state
                and state.revision > previous_state.revision
                and previous_state.top > 1
                and state.top <= 1
            ):
                result.refresh_count += 1
                result.last_scroll_state = {
                    "reset_detected": True,
                    "evidence": "content_revision_and_scroll_reset",
                    "from_top": previous_state.top,
                    "to_top": state.top,
                    "revision": state.revision,
                }
                if result.refresh_count >= self.reset_threshold and interval.granularity == "day":
                    result.reason = "history_refresh_unstable"
                    return result
            elif (
                window_start is not None
                and previous_window_start is not None
                and window_start < previous_window_start
            ):
                result.refresh_count += 1
                result.last_scroll_state = {
                    "reset_detected": True,
                    "evidence": "visible_row_window_moved_backwards",
                    "previous_window_start": previous_window_start,
                    "current_window_start": window_start,
                    "visible_ids_before": list(previous_ids),
                    "visible_ids_after": list(current_ids),
                }
                if result.refresh_count >= self.reset_threshold and interval.granularity == "day":
                    result.reason = "history_refresh_unstable"
                    return result
            if state.at_bottom:
                if not await self.adapter.wait_settled():
                    result.reason = "history_refresh_unstable"
                    return result
                confirm_state = await self.adapter.scroll_state()
                confirm_rows = await self.adapter.visible_rows()
                confirm_ids = tuple(
                    str(row.get("trade_id", "")).strip()
                    for row in confirm_rows
                    if str(row.get("trade_id", "")).strip()
                )
                if (
                    confirm_state.at_bottom
                    and confirm_state == state
                    and confirm_ids == current_ids
                ):
                    result.bottom_reached = True
                    return result

            if previous_state == state and current_ids == previous_ids:
                stagnant_steps += 1
                if stagnant_steps >= 3:
                    result.reason = "bottom_not_reached"
                    return result
            else:
                stagnant_steps = 0
            previous_state = state
            previous_ids = current_ids
            previous_window_start = window_start
            await self.adapter.scroll_down()

        result.reason = "bottom_not_reached"
        return result

    async def _persist_rows(
        self,
        interval: ExportInterval,
        rows: list[dict[str, Any]],
        result: "_ScanResult",
        *,
        persist: bool,
    ) -> tuple[int, int, bool]:
        new_rows = []
        new_timestamps: dict[str, str] = {}
        duplicates = 0
        for row in rows:
            if self.store.contains_trade(row["trade_id"]):
                if self.store.trade_timestamp(row["trade_id"]) != row["opened_at_utc"]:
                    raise TradeIdentityConflict(row["trade_id"])
                duplicates += 1
            elif all(item["trade_id"] != row["trade_id"] for item in new_rows):
                new_rows.append(row)
                new_timestamps[row["trade_id"]] = row["opened_at_utc"]
            else:
                if new_timestamps[row["trade_id"]] != row["opened_at_utc"]:
                    raise TradeIdentityConflict(row["trade_id"])
                duplicates += 1

        remaining = self.unique_limit - self.store.unique_count()
        overflow = len(new_rows) > remaining
        selected = new_rows[:remaining]
        if selected and persist:
            try:
                inserted, dupes = self.store.upsert_batch(interval, selected)
            except (OSError, sqlite3.Error) as error:
                raise PersistenceError from error
            result.new_ids.update(row["trade_id"] for row in selected)
            if self.batch_callback:
                await self.batch_callback(interval.interval_id, inserted, dupes)
            duplicates += dupes
        elif new_rows and not persist:
            result.new_ids.update(row["trade_id"] for row in new_rows)
        return len(selected), duplicates, overflow

    def _result(
        self,
        interval: ExportInterval,
        status: str,
        reason: str | None,
        duplicates: int,
        refreshes: int,
        scan: "_ScanResult | None" = None,
    ) -> IntervalResult:
        result = IntervalResult(
            interval_id=interval.interval_id,
            status=status,
            reason=reason,
            unique_count=scan.unique_count if scan else 0,
            duplicate_count=duplicates + (scan.duplicate_count if scan else 0),
            refresh_count=refreshes + (scan.refresh_count if scan else 0),
            bottom_reached=scan.bottom_reached if scan else False,
            verified=status == "completed",
        )
        self.store.checkpoint(
            interval,
            status,
            unique_count=self.store.unique_count(),
            duplicate_count=result.duplicate_count,
            refresh_count=result.refresh_count,
            scroll_state=scan.last_scroll_state if scan else {},
            verification=reason or ("verified" if status == "completed" else "incomplete"),
        )
        return result


@dataclass
class _ScanResult:
    unique_count: int = 0
    duplicate_count: int = 0
    refresh_count: int = 0
    bottom_reached: bool = False
    reason: str | None = None
    new_ids: set[str] = field(default_factory=set)
    seen_ids: set[str] = field(default_factory=set)
    last_scroll_state: dict[str, Any] = field(default_factory=dict)


class MissingTradeIdError(ValueError):
    pass


class TradeTimestampError(ValueError):
    pass


class TradeIdentityConflict(ValueError):
    pass


class PersistenceError(OSError):
    pass


def _parse_trade(
    raw: dict[str, str],
    timezone: ZoneInfo,
    interval: ExportInterval,
    *,
    timestamp_format: str | None = None,
) -> dict[str, Any] | None:
    displayed_timestamp = str(raw.get("timestamp", "")).strip()
    try:
        opened = datetime.fromisoformat(displayed_timestamp.replace("Z", "+00:00"))
    except ValueError:
        if not timestamp_format:
            raise TradeTimestampError("invalid_trade_timestamp") from None
        try:
            opened = datetime.strptime(displayed_timestamp, timestamp_format)
        except ValueError as error:
            raise TradeTimestampError("invalid_trade_timestamp") from error
    if opened.tzinfo is None or opened.utcoffset() is None:
        first_fold = opened.replace(tzinfo=timezone, fold=0)
        second_fold = opened.replace(tzinfo=timezone, fold=1)
        if first_fold.utcoffset() != second_fold.utcoffset():
            first_valid = (
                first_fold.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) == opened
            )
            second_valid = (
                second_fold.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) == opened
            )
            if first_valid and second_valid:
                raise TradeTimestampError("ambiguous_local_timestamp")
            if not first_valid and not second_valid:
                raise TradeTimestampError("nonexistent_local_timestamp")
        opened = first_fold
    if not interval.contains(opened):
        return None
    trade_id = str(raw.get("trade_id", "")).strip()
    if not trade_id:
        raise MissingTradeIdError("History row has no stable trade ID")
    normalized = opened.astimezone(UTC)
    return {
        "trade_id": trade_id,
        "opened_at_utc": normalized.isoformat(),
        "displayed_timestamp": displayed_timestamp,
        "ui_timezone": str(timezone),
        "raw_fields": raw,
    }
