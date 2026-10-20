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
        else:
            result = asyncio.run(
                client.get_eurusd_m1_candles(
                    arguments.start,
                    arguments.end,
                    arguments.account_id,
                )
            )
        print(json.dumps(result, indent=2))
        return 0
    except (OAuthError, CTraderError) as error:
        print(f"cTrader: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
