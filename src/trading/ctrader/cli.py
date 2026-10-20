import argparse
import asyncio
import getpass
import json
import sys
from datetime import UTC, datetime

from sqlalchemy.exc import SQLAlchemyError

from trading.ctrader.client import CTraderError, CTraderReadOnlyClient
from trading.ctrader.oauth import (
    CTraderSecretStore,
    OAuthError,
    OAuthManager,
    run_browser_authorization,
)
from trading.ctrader.parquet import persist_candles
from trading.ctrader.postgres import import_api_candles, import_parquet_dataset
from trading.ctrader.quality import validate_candle_data
from trading.ctrader.sync import MAX_SYNC_DAYS, CTraderSyncService
from trading.data.repository import PostgresMarketDataRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctrader", description="Read-only cTrader Open API tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("configure", help="Store cTrader application credentials securely")
    commands.add_parser("auth", help="Authorize read-only account access")
    parquet_import = commands.add_parser(
        "import-parquet",
        help="Revalidate and import a cTrader Parquet dataset into PostgreSQL",
    )
    parquet_import.add_argument("--input", required=True, help="cTrader Parquet dataset root")
    sync = commands.add_parser(
        "sync",
        help="Find and synchronize missing EURUSD M1 demo candles",
    )
    sync.add_argument("--instrument", choices=["EURUSD"], required=True)
    sync.add_argument("--environment", choices=["demo"], required=True)
    sync.add_argument("--timeframe", choices=["M1"], required=True)
    sync.add_argument("--start", type=_parse_utc_datetime, required=True)
    sync.add_argument("--end", type=_parse_utc_datetime, required=True)
    sync.add_argument("--account-id", type=int)
    sync.add_argument(
        "--max-days",
        type=int,
        default=MAX_SYNC_DAYS,
        help=f"Maximum requested window length in days (1-{MAX_SYNC_DAYS})",
    )
    sync.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect PostgreSQL and print the missing-range plan without API requests or writes",
    )
    sync.add_argument(
        "--save-postgres",
        action="store_true",
        help="Explicitly allow validated missing candles to be inserted into PostgreSQL",
    )
    sync.add_argument("--json", action="store_true", help="Print a machine-readable JSON summary")
    accounts = commands.add_parser("accounts", help="List accessible demo accounts")
    accounts.add_argument("--environment", choices=["demo"], required=True)
    symbols = commands.add_parser("symbols", help="Discover EURUSD demo symbol metadata")
    symbols.add_argument("--instrument", choices=["EURUSD"], required=True)
    symbols.add_argument("--environment", choices=["demo"], required=True)
    symbols.add_argument("--account-id", type=int)
    candles = commands.add_parser("candles", help="Retrieve EURUSD M1 demo candles")
    candles.add_argument("--instrument", choices=["EURUSD"], required=True)
    candles.add_argument("--environment", choices=["demo"], required=True)
    candles.add_argument("--start", type=_parse_utc_datetime, required=True)
    candles.add_argument("--end", type=_parse_utc_datetime, required=True)
    candles.add_argument("--account-id", type=int)
    candles.add_argument(
        "--multi-day",
        action="store_true",
        help="Split a longer range into 24-hour chunks (maximum 31 days)",
    )
    candles.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print safe timestamp metadata to stderr if a candle is outside the requested range",
    )
    candles.add_argument(
        "--quality-report",
        action="store_true",
        help="Include a structured candle-quality report in JSON output",
    )
    candles.add_argument(
        "--save-parquet",
        action="store_true",
        help="Opt in to saving validated candles in a new Parquet dataset",
    )
    candles.add_argument(
        "--import-postgres",
        action="store_true",
        help="Opt in to importing validated candles into PostgreSQL",
    )
    candles.add_argument(
        "--parquet-root",
        default="data/ctrader-parquet",
        help="Output dataset root used with --save-parquet (must not already exist)",
    )
    return parser


def _parse_utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError("timestamp must be ISO-8601 UTC") from error
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise argparse.ArgumentTypeError("timestamp must include the UTC timezone")
    return parsed.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "import-parquet":
            from trading.db import get_engine

            quality, import_report = import_parquet_dataset(
                PostgresMarketDataRepository(get_engine()),
                arguments.input,
            )
            print(
                json.dumps(
                    {
                        "quality_report": quality,
                        "postgres_import": import_report.as_dict(),
                    },
                    indent=2,
                )
            )
            return int(
                quality["validation_status"] == "invalid"
                or import_report.conflicting > 0
                or import_report.rejected > 0
            )

        if arguments.command == "sync":
            from trading.db import get_engine

            service = CTraderSyncService(
                PostgresMarketDataRepository(get_engine()),
                account_id=arguments.account_id,
            )
            if not arguments.dry_run:
                manager = OAuthManager(CTraderSecretStore())
                client_id, client_secret = manager.credentials()
                access_token = manager.access_token()
                service.client = CTraderReadOnlyClient(client_id, client_secret, access_token)
            summary = asyncio.run(
                service.synchronize(
                    arguments.start,
                    arguments.end,
                    max_days=arguments.max_days,
                    dry_run=arguments.dry_run,
                    save_postgres=arguments.save_postgres,
                )
            )
            if arguments.json:
                print(json.dumps(summary, indent=2))
            else:
                print(_format_sync_summary(summary))
            return int(summary["status"] == "incomplete")

        manager = OAuthManager(CTraderSecretStore())
        if arguments.command == "configure":
            client_id = input("cTrader Client ID: ")
            client_secret = getpass.getpass("cTrader Client Secret: ")
            manager.configure(client_id, client_secret)
            print("cTrader credentials stored in the operating system credential vault.")
            return 0
        if arguments.command == "auth":
            client_id, _ = manager.credentials()
            code = run_browser_authorization(client_id)
            manager.authorize(code)
            print("cTrader account authorization completed; tokens stored securely.")
            return 0

        client_id, client_secret = manager.credentials()
        access_token = manager.access_token()
        client = CTraderReadOnlyClient(client_id, client_secret, access_token)
        if arguments.command == "accounts":
            result = asyncio.run(client.list_demo_accounts())
        elif arguments.command == "symbols":
            result = asyncio.run(client.discover_eurusd(arguments.account_id))
        elif arguments.multi_day:
            result = asyncio.run(
                client.get_eurusd_m1_candles_multi_day(
                    arguments.start,
                    arguments.end,
                    arguments.account_id,
                    diagnostics=arguments.diagnostics,
                )
            )
        else:
            result = asyncio.run(
                client.get_eurusd_m1_candles(
                    arguments.start,
                    arguments.end,
                    arguments.account_id,
                    diagnostics=arguments.diagnostics,
                )
            )
        if arguments.command == "candles":
            import_report = None
            if arguments.import_postgres:
                from trading.db import get_engine

                validated_candles, quality_report, import_report = import_api_candles(
                    PostgresMarketDataRepository(get_engine()),
                    result,
                    arguments.start,
                    arguments.end,
                )
            else:
                validated_candles, quality_report = validate_candle_data(
                    result,
                    arguments.start,
                    arguments.end,
                )
            save_rejected = False
            if arguments.save_parquet:
                if quality_report["validation_status"] == "invalid":
                    print(
                        "cTrader: refusing Parquet persistence because "
                        "candle quality validation failed",
                        file=sys.stderr,
                    )
                    save_rejected = True
                elif validated_candles:
                    try:
                        output_files = persist_candles(
                            validated_candles,
                            arguments.parquet_root,
                            arguments.start,
                            arguments.end,
                        )
                    except (OSError, ValueError) as error:
                        print(f"cTrader: Parquet persistence failed: {error}", file=sys.stderr)
                        return 1
                    print(
                        f"Saved {len(validated_candles)} validated candles to "
                        f"{len(output_files)} Parquet partition(s).",
                        file=sys.stderr,
                    )
                else:
                    print("No candles returned; no Parquet dataset was created.", file=sys.stderr)

            output: object = result
            if arguments.quality_report or arguments.import_postgres:
                output_data: dict[str, object] = {
                    "candles": result,
                    "quality_report": quality_report,
                }
                if import_report is not None:
                    output_data["postgres_import"] = import_report.as_dict()
                output = output_data
            print(json.dumps(output, indent=2))
            if import_report is not None:
                return int(
                    quality_report["validation_status"] == "invalid"
                    or import_report.conflicting > 0
                    or import_report.rejected > 0
                    or save_rejected
                )
            if save_rejected:
                return 1
        return 0
    except (OAuthError, CTraderError) as error:
        print(f"cTrader: {error}", file=sys.stderr)
        return 1
    except (OSError, SQLAlchemyError, ValueError) as error:
        print(f"cTrader: {error}", file=sys.stderr)
        return 1


def _format_sync_summary(summary: dict[str, object]) -> str:
    requested = summary["requested_interval"]
    lines = [
        f"Status: {summary['status']}",
        f"Instrument/timeframe: {summary['instrument']} {summary['timeframe']}",
        f"Requested UTC interval: {requested['start']} to {requested['end']} [start, end)",
        f"Existing candles: {summary['existing_candle_count']}",
        f"Missing candles identified: {summary['missing_candle_count']}",
        f"Missing intervals: {len(summary['missing_intervals_identified'])}",
        f"API requests attempted: {summary['api_requests_attempted']}",
        f"Candles retrieved/validated: {summary['candles_retrieved']}/"
        f"{summary['candles_validated']}",
        f"Inserted/unchanged: {summary['candles_inserted']}/{summary['unchanged_records']}",
        f"Conflicts/rejected: {summary['conflicts']}/{summary['rejected_records']}",
        f"Unresolved gaps: {len(summary['unresolved_gaps'])}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
