import argparse
import asyncio
import getpass
import json
import sys
from datetime import UTC, datetime

from trading.ctrader.client import CTraderError, CTraderReadOnlyClient
from trading.ctrader.oauth import (
    CTraderSecretStore,
    OAuthError,
    OAuthManager,
    run_browser_authorization,
)
from trading.ctrader.parquet import persist_candles
from trading.ctrader.quality import validate_candle_data


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ctrader", description="Read-only cTrader Open API tools")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("configure", help="Store cTrader application credentials securely")
    commands.add_parser("auth", help="Authorize read-only account access")
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
            validated_candles, quality_report = validate_candle_data(
                result,
                arguments.start,
                arguments.end,
            )
            if arguments.save_parquet:
                if quality_report["validation_status"] == "invalid":
                    print(
                        "cTrader: refusing Parquet persistence because "
                        "candle quality validation failed",
                        file=sys.stderr,
                    )
                    return 1
                if validated_candles:
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
            if arguments.quality_report:
                output = {"candles": result, "quality_report": quality_report}
            print(json.dumps(output, indent=2))
        return 0
    except (OAuthError, CTraderError) as error:
        print(f"cTrader: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
