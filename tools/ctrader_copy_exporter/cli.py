import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tools.ctrader_copy_exporter.exporter import DryRunExporter
from tools.ctrader_copy_exporter.playwright_adapter import (
    PlaywrightHistoryAdapter,
    SelectorConfig,
    launch_persistent_browser,
    run_interactive_diagnostic,
)
from tools.ctrader_copy_exporter.schedule import require_single_week, weekly_intervals
from tools.ctrader_copy_exporter.store import ExportStore


def parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Invalid ISO-8601 timestamp: {value}") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise argparse.ArgumentTypeError("Range timestamps must include a UTC offset")
    return timestamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only cTrader Copy History exporter (one-week dry run only)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    dry_run = subparsers.add_parser("dry-run", help="Export one local week, capped at 20 trades")
    dry_run.add_argument("--url", required=True, help="Authorized cTrader Copy page URL")
    dry_run.add_argument("--start", required=True, type=parse_timestamp)
    dry_run.add_argument("--end", required=True, type=parse_timestamp)
    dry_run.add_argument("--timezone", required=True, help="UI IANA timezone, e.g. Europe/London")
    dry_run.add_argument(
        "--selectors",
        type=Path,
        required=True,
        help="Operator-validated visible UI selector JSON",
    )
    dry_run.add_argument("--output", type=Path, default=Path("data/ctrader_copy_export"))
    dry_run.add_argument("--no-csv", action="store_true", help="Do not create a CSV projection")
    dry_run.add_argument("--profile", type=Path, default=Path("data/ctrader-browser-profile"))
    dry_run.add_argument("--account-label", default="authorized-account")
    dry_run.add_argument("--run-id", help="Reuse to resume this same dry-run checkpoint")
    dry_run.add_argument("--weekly-trade-threshold", type=int, default=5000)
    dry_run.add_argument("--reset-threshold", type=int, default=3)
    dry_run.add_argument("--max-scroll-steps", type=int, default=10000)

    diagnostic = subparsers.add_parser(
        "diagnostic", help="Interactively inspect visible controls and scrollable candidates"
    )
    diagnostic.add_argument("--url", required=True, help="Authorized cTrader page URL")
    diagnostic.add_argument("--profile", type=Path, default=Path("data/ctrader-browser-profile"))
    diagnostic.add_argument(
        "--output",
        type=Path,
        default=Path("data/ctrader-copy-dom-diagnostic.json"),
    )
    return parser


async def run_dry_run(args: argparse.Namespace) -> dict:
    try:
        timezone = ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"Unknown IANA timezone: {args.timezone}") from error
    interval = require_single_week(args.start, args.end, timezone)
    selectors = SelectorConfig.from_file(args.selectors)
    request = {
        "mode": "dry-run",
        "account_label": args.account_label,
        "timezone": args.timezone,
        "requested_start": args.start.isoformat(),
        "requested_end": args.end.isoformat(),
        "weekly_intervals": [
            {
                "id": item.interval_id,
                "start": item.start.isoformat(),
                "end": item.end.isoformat(),
                "ui_start": item.ui_start,
                "ui_end": item.ui_end,
            }
            for item in weekly_intervals(args.start, args.end, timezone)
        ],
        "unique_limit": 20,
        "csv_enabled": not args.no_csv,
        "weekly_trade_threshold": args.weekly_trade_threshold,
        "reset_threshold": args.reset_threshold,
        "selector_fingerprint": hashlib.sha256(args.selectors.read_bytes()).hexdigest(),
    }
    digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()[:16]
    run_id = args.run_id or f"dry-run-{digest}"
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id) is None:
        raise ValueError("--run-id may contain only letters, digits, hyphens, and underscores")
    output_dir = args.output / run_id
    store = ExportStore(output_dir, run_id, csv_enabled=not args.no_csv)
    previous = store.manifest()
    previous_request = previous.get("request", previous) if previous else None
    if previous_request and previous_request != request:
        store.close()
        raise ValueError("Run ID already exists with different range/configuration")
    store.save_manifest({"request": request})

    playwright = None
    context = None
    try:
        playwright, context, page = await launch_persistent_browser(args.profile)
        await page.goto(args.url, wait_until="domcontentloaded")
        print(
            "Use only an account/history you are authorized to access. Sign in manually if "
            "needed, navigate to Copy > History, and press Enter to begin the one-week "
            "20-trade-capped dry run."
        )
        await asyncio.to_thread(input)
        exporter = DryRunExporter(
            PlaywrightHistoryAdapter(page, selectors),
            store,
            timezone,
            weekly_trade_threshold=args.weekly_trade_threshold,
            reset_threshold=args.reset_threshold,
            max_scroll_steps=args.max_scroll_steps,
            timestamp_format=selectors.timestamp_format,
        )
        summary = await exporter.run_week(interval)
        summary["account_label"] = args.account_label
        summary["manifest_path"] = str(store.database_path)
        store.save_manifest({"request": request, "summary": summary})
        return summary
    finally:
        if context:
            await context.close()
        if playwright:
            await playwright.stop()
        store.close()


async def run_diagnostic(args: argparse.Namespace) -> dict:
    return await run_interactive_diagnostic(args.url, args.profile)


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "dry-run":
        result = asyncio.run(run_dry_run(args))
    else:
        result = asyncio.run(run_diagnostic(args))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        result["diagnostic_path"] = str(args.output)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
