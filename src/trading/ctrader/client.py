import asyncio
import json
import re
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import websockets

from trading.data.domain import Candle, InstrumentCode, PriceType, Timeframe

DEMO_WEBSOCKET_URL = "wss://demo.ctraderapi.com:5036"
DEFAULT_MAX_MESSAGE_SIZE_BYTES = 8 * 1024 * 1024
MAX_ALLOWED_MESSAGE_SIZE_BYTES = 16 * 1024 * 1024
MAX_MULTI_DAY_CANDLE_DURATION = timedelta(days=31)
MAX_CANDLE_CHUNK_ATTEMPTS = 3
BASE_CANDLE_RETRY_DELAY_SECONDS = 1.0
MAX_CANDLE_RETRY_DELAY_SECONDS = 60.0
APPLICATION_AUTH = (2100, 2101)
ACCOUNT_AUTH = (2102, 2103)
SYMBOLS_LIST = (2114, 2115)
SYMBOL_BY_ID = (2116, 2117)
TRADER = (2121, 2122)
ACCOUNT_LIST = (2149, 2150)
TRENDBARS = (2137, 2138)
TRENDBAR_PRICE_SCALE = Decimal(100_000)
MAX_CANDLE_REQUEST_INTERVAL = timedelta(days=1)
ERROR_RESPONSE = 50
OPEN_API_ERROR_RESPONSE = 2142
HEARTBEAT_EVENT = 51
HEARTBEAT_INTERVAL_SECONDS = 10
ERROR_RESPONSE_TYPES = {ERROR_RESPONSE, OPEN_API_ERROR_RESPONSE}
UNSOLICITED_EVENT_TYPES = {
    HEARTBEAT_EVENT,
    2107,
    2120,
    2123,
    2126,
    2131,
    2132,
    2141,
    2147,
    2148,
    2155,
    2164,
    2171,
    2172,
}
TERMINAL_EVENT_TYPES = {
    2147: "access_token_invalidated",
    2148: "client_disconnected",
    2164: "account_disconnected",
}
REQUEST_STAGES = {
    APPLICATION_AUTH[0]: "application_auth",
    ACCOUNT_LIST[0]: "account_list",
    ACCOUNT_AUTH[0]: "account_auth",
    TRADER[0]: "trader_metadata",
    SYMBOLS_LIST[0]: "symbols_list",
    SYMBOL_BY_ID[0]: "symbol_details",
    TRENDBARS[0]: "historical_trendbars",
}
READ_ONLY_MESSAGES = {
    APPLICATION_AUTH[0]: APPLICATION_AUTH[1],
    ACCOUNT_AUTH[0]: ACCOUNT_AUTH[1],
    SYMBOLS_LIST[0]: SYMBOLS_LIST[1],
    SYMBOL_BY_ID[0]: SYMBOL_BY_ID[1],
    TRADER[0]: TRADER[1],
    ACCOUNT_LIST[0]: ACCOUNT_LIST[1],
    TRENDBARS[0]: TRENDBARS[1],
}


class CTraderError(Exception):
    """Sanitized error raised by the read-only cTrader client."""


class _RetryableCTraderError(CTraderError):
    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class CTraderReadOnlyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        access_token: str,
        connect: Callable = websockets.connect,
        request_timeout: float = 10,
        connection_attempts: int = 3,
        sleep: Callable = asyncio.sleep,
        max_message_size_bytes: int = DEFAULT_MAX_MESSAGE_SIZE_BYTES,
    ) -> None:
        if not client_id or not client_secret or not access_token:
            raise CTraderError("cTrader credentials and authorization are required")
        if (
            isinstance(max_message_size_bytes, bool)
            or not isinstance(max_message_size_bytes, int)
            or not 1 <= max_message_size_bytes <= MAX_ALLOWED_MESSAGE_SIZE_BYTES
        ):
            raise ValueError("max_message_size_bytes must be between 1 byte and 16 MiB")
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token = access_token
        self._connect = connect
        self._request_timeout = request_timeout
        self._connection_attempts = connection_attempts
        self._sleep = sleep
        self._max_message_size_bytes = max_message_size_bytes
        self._send_lock = asyncio.Lock()
        self._heartbeat_task = None
        self._heartbeat_error = None

    async def list_demo_accounts(self) -> list[dict]:
        async with self._connection() as websocket:
            await self._authenticate_application(websocket)
            accounts = await self._request(
                websocket, *ACCOUNT_LIST, {"accessToken": self._access_token}
            )
        account_items = _demo_account_items(accounts)
        return [
            {
                "account_id": _account_id(account["ctidTraderAccountId"]),
                "is_live": False,
            }
            for account in account_items
            if account.get("isLive") is False
        ]

    async def discover_eurusd(self, account_id: int | None = None) -> dict:
        metadata = await self.discover_instrument(InstrumentCode.EURUSD, "EURUSD", account_id)
        # Preserve the existing EURUSD CLI contract; generic discovery exposes specs.
        metadata["symbol"] = {k: v for k, v in metadata["symbol"].items()
                              if k not in {"enabled", "description", "specifications"}}
        return metadata

    async def list_symbols(self, account_id: int) -> list[dict]:
        async with self._connection() as websocket:
            await self._authenticate_application(websocket)
            response = await self._request(websocket, *ACCOUNT_AUTH, {
                "ctidTraderAccountId": account_id, "accessToken": self._access_token,
            })
            _verify_account_authorization(response, account_id)
            response = await self._request(websocket, *SYMBOLS_LIST, {
                "ctidTraderAccountId": account_id, "includeArchivedSymbols": False,
            })
        return response["symbol"]

    async def discover_instrument(
        self, instrument: InstrumentCode, broker_symbol: str, account_id: int | None = None,
    ) -> dict:
        async with self._connection() as websocket:
            await self._authenticate_application(websocket)
            accounts_response = await self._request(
                websocket,
                *ACCOUNT_LIST,
                {"accessToken": self._access_token},
            )
            demo_accounts = _demo_account_items(accounts_response)
            selected = self._select_demo_account(demo_accounts, account_id)
            selected_id = _account_id(selected["ctidTraderAccountId"])
            account_auth_response = await self._request(
                websocket,
                *ACCOUNT_AUTH,
                {"ctidTraderAccountId": selected_id, "accessToken": self._access_token},
            )
            _verify_account_authorization(account_auth_response, selected_id)
            trader_response = await self._request(
                websocket,
                *TRADER,
                {"ctidTraderAccountId": selected_id},
            )
            trader = trader_response.get("trader", {})
            if not isinstance(trader, dict):
                raise CTraderError("cTrader returned malformed account metadata")
            symbols_response = await self._request(
                websocket,
                *SYMBOLS_LIST,
                {"ctidTraderAccountId": selected_id, "includeArchivedSymbols": False},
                diagnostic_context={
                    "account_auth_completed": True,
                    "account_id_verified": True,
                },
            )
            symbol_items = symbols_response.get("symbol", [])
            if not isinstance(symbol_items, list) or any(
                not isinstance(symbol, dict) for symbol in symbol_items
            ):
                raise CTraderError("cTrader returned malformed symbol data")
            matches = [
                symbol
                for symbol in symbol_items
                if isinstance(symbol.get("symbolName"), str)
                and _normalize_symbol(symbol["symbolName"]) == _normalize_symbol(broker_symbol)
            ]
            if not matches:
                raise CTraderError("EURUSD was not found in the selected demo account")
            if len(matches) != 1:
                raise CTraderError("EURUSD symbol discovery is ambiguous; no mapping was selected")
            symbol = matches[0]
            if not symbol.get("symbolId"):
                raise CTraderError("cTrader returned EURUSD without a broker symbol ID")
            symbol_id = _account_id(symbol["symbolId"])
            details_response = await self._request(
                websocket,
                *SYMBOL_BY_ID,
                {"ctidTraderAccountId": selected_id, "symbolId": [symbol_id]},
            )
            detail_values = details_response.get("symbol", [])
            if not isinstance(detail_values, list) or not detail_values:
                raise CTraderError("cTrader returned no EURUSD symbol metadata")
            detail = detail_values[0]
            if not isinstance(detail, dict):
                raise CTraderError("cTrader returned malformed EURUSD symbol metadata")
            if detail.get("symbolId") is not None and _account_id(detail["symbolId"]) != symbol_id:
                raise CTraderError("cTrader returned metadata for a different symbol")
        return {
            "account": {
                "account_id": selected_id,
                "is_live": False,
                "broker_name": trader.get("brokerName"),
                "account_type": trader.get("accountType"),
                "trader_login": trader.get("traderLogin"),
            },
            "symbol": {
                "internal_instrument": instrument.value,
                "broker_symbol_name": symbol.get("symbolName"),
                "broker_symbol_id": symbol.get("symbolId"),
                "account_id": selected_id,
                "environment": "demo",
                "digits": detail.get("digits"),
                "pip_position": detail.get("pipPosition"),
                "enabled": symbol.get("enabled"),
                "description": symbol.get("description"),
                "specifications": detail,
            },
        }

    async def get_eurusd_m1_candles(
        self,
        start: datetime,
        end: datetime,
        account_id: int | None = None,
        *,
        diagnostics: bool = False,
        request_observer: Callable[[], None] | None = None,
    ) -> list[dict]:
        return await self.get_m1_candles(
            InstrumentCode.EURUSD, start, end, account_id,
            diagnostics=diagnostics, request_observer=request_observer,
        )

    async def get_m1_candles(
        self, instrument: InstrumentCode, start: datetime, end: datetime,
        account_id: int | None = None, *, broker_symbol: str | None = None,
        diagnostics: bool = False, request_observer: Callable[[], None] | None = None,
    ) -> list[dict]:
        start, end = _validate_candle_range(start, end)
        symbol_metadata = (
            await self.discover_eurusd(account_id) if instrument == InstrumentCode.EURUSD
            else await self.discover_instrument(instrument, broker_symbol or instrument.value,
                                                account_id)
        )
        selected_id = symbol_metadata["account"]["account_id"]
        symbol = symbol_metadata["symbol"]
        symbol_id = _account_id(symbol["broker_symbol_id"])
        digits = _symbol_digits(symbol.get("digits"))

        async with self._connection() as websocket:
            await self._authenticate_application(websocket)
            account_auth = await self._request(
                websocket,
                *ACCOUNT_AUTH,
                {"ctidTraderAccountId": selected_id, "accessToken": self._access_token},
            )
            _verify_account_authorization(account_auth, selected_id)
            # count limits bars backward from toTimestamp; omit it to keep sparse
            # ranges bounded by fromTimestamp.
            if request_observer is not None:
                request_observer()
            response = await self._request(
                websocket,
                *TRENDBARS,
                {
                    "ctidTraderAccountId": selected_id,
                    "fromTimestamp": int(start.timestamp()) * 1000,
                    "toTimestamp": int(end.timestamp()) * 1000 - 1,
                    "period": Timeframe.M1.value,
                    "symbolId": symbol_id,
                },
                diagnostic_context={
                    "account_auth_completed": True,
                    "account_id_verified": True,
                },
            )
        if _account_id(response.get("ctidTraderAccountId")) != selected_id:
            raise CTraderError(
                "cTrader trendbar response belongs to a different account "
                "(stage=historical_trendbars, account_id_match=false)"
            )
        response_symbol_id = response.get("symbolId")
        if response_symbol_id is not None and _account_id(response_symbol_id) != symbol_id:
            raise CTraderError(
                "cTrader trendbar response belongs to a different symbol "
                "(stage=historical_trendbars, symbol_id_match=false)"
            )
        return _decode_m1_trendbars(
            response,
            start,
            end,
            digits,
            diagnostics=diagnostics,
            instrument=instrument,
            broker_symbol=symbol["broker_symbol_name"],
            broker_symbol_id=symbol_id,
        )

    async def get_eurusd_m1_candles_multi_day(
        self,
        start: datetime,
        end: datetime,
        account_id: int | None = None,
        *,
        diagnostics: bool = False,
        request_observer: Callable[[], None] | None = None,
        instrument: InstrumentCode = InstrumentCode.EURUSD,
        broker_symbol: str | None = None,
    ) -> list[dict]:
        start, end = _validate_candle_range(
            start,
            end,
            max_duration=MAX_MULTI_DAY_CANDLE_DURATION,
        )
        candles = []
        seen_timestamps = set()
        previous_timestamp = None
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + MAX_CANDLE_REQUEST_INTERVAL, end)
            for attempt in range(MAX_CANDLE_CHUNK_ATTEMPTS):
                try:
                    if instrument != InstrumentCode.EURUSD:
                        chunk = await self.get_m1_candles(
                            instrument, chunk_start, chunk_end, account_id,
                            broker_symbol=broker_symbol, diagnostics=diagnostics,
                            request_observer=request_observer,
                        )
                    elif request_observer is None:
                        chunk = await self.get_eurusd_m1_candles(
                            chunk_start,
                            chunk_end,
                            account_id,
                            diagnostics=diagnostics,
                        )
                    else:
                        chunk = await self.get_eurusd_m1_candles(
                            chunk_start,
                            chunk_end,
                            account_id,
                            diagnostics=diagnostics,
                            request_observer=request_observer,
                        )
                    break
                except _RetryableCTraderError as error:
                    if attempt + 1 >= MAX_CANDLE_CHUNK_ATTEMPTS:
                        raise
                    delay = max(
                        BASE_CANDLE_RETRY_DELAY_SECONDS * (2**attempt),
                        float(error.retry_after_seconds or 0),
                    )
                    if delay > MAX_CANDLE_RETRY_DELAY_SECONDS:
                        raise
                    print(
                        "cTrader: retrying historical candle chunk "
                        f"(attempt={attempt + 2}/{MAX_CANDLE_CHUNK_ATTEMPTS}, "
                        f"delay_seconds={delay:g})",
                        file=sys.stderr,
                    )
                    await self._sleep(delay)

            for candle in chunk:
                if not isinstance(candle, Mapping):
                    raise CTraderError(
                        "cTrader returned a malformed candle entry (stage=historical_trendbars)"
                    )
                timestamp = _candle_timestamp(candle)
                if timestamp in seen_timestamps:
                    raise CTraderError(
                        "cTrader returned duplicate candles during multi-day retrieval "
                        "(stage=historical_trendbars)"
                    )
                if previous_timestamp is not None and timestamp < previous_timestamp:
                    raise CTraderError(
                        "cTrader returned candles out of chronological order "
                        "during multi-day retrieval "
                        "(stage=historical_trendbars)"
                    )
                seen_timestamps.add(timestamp)
                previous_timestamp = timestamp
                candles.append(candle)
            chunk_start = chunk_end
        return candles

    async def _authenticate_application(self, websocket) -> None:
        await self._request(
            websocket,
            *APPLICATION_AUTH,
            {"clientId": self._client_id, "clientSecret": self._client_secret},
        )

    async def _request(
        self,
        websocket,
        request_type: int,
        response_type: int,
        payload: dict,
        diagnostic_context: Mapping[str, bool] | None = None,
    ) -> dict:
        if READ_ONLY_MESSAGES.get(request_type) != response_type:
            raise CTraderError("cTrader request is outside the read-only API subset")
        client_message_id = str(uuid.uuid4())
        message = {
            "clientMsgId": client_message_id,
            "payloadType": request_type,
            "payload": payload,
        }
        stage = REQUEST_STAGES[request_type]
        context_text = _diagnostic_context(diagnostic_context)
        try:
            async with self._send_lock:
                await asyncio.wait_for(
                    websocket.send(json.dumps(message)),
                    timeout=self._request_timeout,
                )
        except TimeoutError as error:
            raise _RetryableCTraderError(
                "cTrader request send timed out "
                f"(stage={stage}, request_payload_type={request_type}, "
                f"request_send_completed=false{context_text})"
            ) from error
        except websockets.ConnectionClosed as error:
            close_details = _close_metadata(error)
            raise _RetryableCTraderError(
                "cTrader connection closed while sending a request "
                f"(stage={stage}, request_payload_type={request_type}, "
                f"request_send_completed=false, {close_details}{context_text})"
            ) from error
        except OSError as error:
            raise _RetryableCTraderError(
                "cTrader request send failed "
                f"(stage={stage}, request_payload_type={request_type}, "
                f"request_send_completed=false{context_text})"
            ) from error
        except websockets.WebSocketException as error:
            raise CTraderError(
                "cTrader request send failed "
                f"(stage={stage}, request_payload_type={request_type}, "
                f"request_send_completed=false{context_text})"
            ) from error
        last_payload_type = None
        last_correlation = None
        while True:
            try:
                wire_message = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=self._request_timeout,
                )
                if not isinstance(wire_message, (str, bytes, bytearray)):
                    raise CTraderError(
                        "cTrader returned an invalid WebSocket message "
                        f"(expected_payload_type={response_type}, "
                        f"received_wire_type={type(wire_message).__name__})"
                    )
                response = json.loads(wire_message)
            except json.JSONDecodeError as error:
                raise CTraderError(
                    "cTrader returned invalid JSON "
                    f"(expected_payload_type={response_type}, response_format=json)"
                ) from error
            except TypeError as error:
                raise CTraderError(
                    "cTrader returned an invalid WebSocket message "
                    f"(expected_payload_type={response_type}, received_wire_type=unsupported)"
                ) from error
            except (TimeoutError, OSError) as error:
                unmatched = ""
                if last_payload_type is not None:
                    unmatched = (
                        f", last_response_payload_type={last_payload_type}, "
                        f"last_response_correlation={last_correlation}"
                    )
                raise _RetryableCTraderError(
                    "cTrader response wait timed out or failed "
                    f"(stage={stage}, request_payload_type={request_type}, "
                    f"request_send_completed=true, expected_payload_type={response_type}"
                    f"{unmatched}{context_text})"
                ) from error
            except UnicodeDecodeError as error:
                unmatched = ""
                if last_payload_type is not None:
                    unmatched = (
                        f", last_response_payload_type={last_payload_type}, "
                        f"last_response_correlation={last_correlation}"
                    )
                raise CTraderError(
                    "cTrader response wait timed out or failed "
                    f"(stage={stage}, request_payload_type={request_type}, "
                    f"request_send_completed=true, expected_payload_type={response_type}"
                    f"{unmatched}{context_text})"
                ) from error
            except websockets.ConnectionClosed as error:
                close_details = _close_metadata(error)
                unmatched = ""
                if last_payload_type is not None:
                    unmatched = (
                        f", last_response_payload_type={last_payload_type}, "
                        f"last_response_correlation={last_correlation}"
                    )
                raise _RetryableCTraderError(
                    "cTrader connection closed while waiting for a response "
                    f"(stage={stage}, request_payload_type={request_type}, "
                    f"request_send_completed=true, expected_payload_type={response_type}, "
                    f"{close_details}"
                    f"{unmatched}{context_text})"
                ) from error
            except websockets.WebSocketException as error:
                raise CTraderError(
                    "cTrader WebSocket receive failed "
                    f"(stage={stage}, request_payload_type={request_type}, "
                    f"request_send_completed=true, expected_payload_type={response_type}"
                    f"{context_text})"
                ) from error
            if not isinstance(response, Mapping):
                raise CTraderError(
                    "cTrader returned a malformed JSON envelope "
                    f"(expected_payload_type={response_type}, "
                    f"received_json_type={type(response).__name__})"
                )
            actual_type = response.get("payloadType")
            if not isinstance(actual_type, int) or isinstance(actual_type, bool):
                raise CTraderError(
                    "cTrader response has an invalid payloadType "
                    f"(expected_payload_type={response_type}, "
                    f"received_json_type={type(actual_type).__name__})"
                )
            if self._heartbeat_error is not None:
                if isinstance(
                    self._heartbeat_error,
                    (OSError, TimeoutError, websockets.ConnectionClosed),
                ):
                    raise _RetryableCTraderError(
                        "cTrader connection heartbeat failed"
                    ) from self._heartbeat_error
                raise CTraderError("cTrader connection heartbeat failed") from self._heartbeat_error
            response_id = response.get("clientMsgId")
            correlation = _correlation_status(response_id, client_message_id)
            last_payload_type = actual_type
            last_correlation = correlation
            if actual_type in TERMINAL_EVENT_TYPES:
                raise CTraderError(
                    "cTrader terminated the active API session "
                    f"(stage={stage}, request_payload_type={request_type}, "
                    f"event_payload_type={actual_type}, "
                    f"event={TERMINAL_EVENT_TYPES[actual_type]}{context_text})"
                )
            if actual_type in ERROR_RESPONSE_TYPES:
                error_payload = response.get("payload")
                error_code = (
                    error_payload.get("errorCode") if isinstance(error_payload, Mapping) else None
                )
                safe_error_code = (
                    error_code
                    if isinstance(error_code, str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", error_code)
                    else "unavailable"
                )
                correlation = "matched" if response_id == client_message_id else "absent"
                if response_id is not None and response_id != client_message_id:
                    correlation = "mismatched"
                error_message = (
                    "cTrader returned an API error "
                    f"(stage={stage}, request_payload_type={request_type}, "
                    f"payload_type={actual_type}, error_code={safe_error_code}, "
                    f"client_msg_id={correlation}{context_text})"
                )
                if safe_error_code == "BLOCKED_PAYLOAD_TYPE":
                    retry_after = (
                        error_payload.get("retryAfter")
                        if isinstance(error_payload, Mapping)
                        else None
                    )
                    if (
                        isinstance(retry_after, int)
                        and not isinstance(retry_after, bool)
                        and retry_after >= 0
                    ):
                        raise _RetryableCTraderError(error_message, retry_after) from None
                    raise _RetryableCTraderError(error_message) from None
                raise CTraderError(error_message)
            if response_id is not None and response_id != client_message_id:
                continue
            if actual_type != response_type:
                if response_id is None and actual_type in UNSOLICITED_EVENT_TYPES:
                    continue
                raise CTraderError(
                    "cTrader returned an unexpected response "
                    f"(stage={stage}, request_payload_type={request_type}, "
                    f"expected_payload_type={response_type}, actual_payload_type={actual_type}, "
                    f"client_msg_id={correlation}{context_text})"
                )
            result = response.get("payload")
            if response_type == APPLICATION_AUTH[1] and result is None:
                return {}
            if not isinstance(result, Mapping):
                raise CTraderError(
                    "cTrader returned a malformed response payload "
                    f"(stage={stage}, payload_type={actual_type}, "
                    f"expected_payload_type={response_type}, "
                    f"payload_state={_payload_state(response)}, "
                    f"{_envelope_metadata(response)}{context_text})"
                )
            return dict(result)

    def _connection(self):
        return _Connection(
            self._connect,
            self._request_timeout,
            self._connection_attempts,
            self._sleep,
            self._send_lock,
            self,
        )

    @staticmethod
    def _select_demo_account(accounts: list[dict], account_id: int | None) -> dict:
        accounts = [account for account in accounts if account.get("isLive") is False]
        if not accounts:
            raise CTraderError(
                "No demo cTrader accounts are available; live accounts are never selected"
            )
        if account_id is not None:
            for account in accounts:
                if str(account.get("ctidTraderAccountId")) == str(account_id):
                    return account
            raise CTraderError("The requested account is not an accessible demo account")
        if len(accounts) != 1:
            raise CTraderError("Multiple demo accounts are available; specify --account-id")
        return accounts[0]


class _Connection:
    def __init__(self, connect, timeout, attempts, sleep, send_lock, client) -> None:
        self._connect = connect
        self._timeout = timeout
        self._attempts = attempts
        self._sleep = sleep
        self._send_lock = send_lock
        self._client = client
        self._manager = None
        self._websocket = None
        self._heartbeat_task = None

    async def __aenter__(self):
        last_error = None
        for attempt in range(self._attempts):
            try:
                self._manager = self._connect(
                    DEMO_WEBSOCKET_URL,
                    open_timeout=self._timeout,
                    close_timeout=5,
                    ping_interval=10,
                    max_size=self._client._max_message_size_bytes,
                )
                self._websocket = await self._manager.__aenter__()
                self._heartbeat_task = asyncio.create_task(self._heartbeat())
                self._client._heartbeat_task = self._heartbeat_task
                return self._websocket
            except (OSError, TimeoutError, websockets.WebSocketException) as error:
                last_error = error
                if attempt + 1 < self._attempts:
                    await self._sleep(0.25 * (2**attempt))
        message = "Unable to connect to the cTrader demo API"
        if isinstance(last_error, (OSError, TimeoutError, websockets.ConnectionClosed)):
            raise _RetryableCTraderError(message) from last_error
        raise CTraderError(message) from last_error

    async def __aexit__(self, exc_type, exc_value, traceback):
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._manager is not None:
            return await self._manager.__aexit__(exc_type, exc_value, traceback)
        return False

    async def _heartbeat(self) -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                async with self._send_lock:
                    await self._websocket.send(json.dumps(_heartbeat_message()))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._client._heartbeat_error = error


def _normalize_symbol(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _validate_candle_range(
    start: datetime,
    end: datetime,
    *,
    max_duration: timedelta = MAX_CANDLE_REQUEST_INTERVAL,
) -> tuple[datetime, datetime]:
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        raise CTraderError("cTrader candle range must use timezone-aware UTC datetimes")
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
    ):
        raise CTraderError("cTrader candle range must use UTC timestamps")
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if start.second or start.microsecond or end.second or end.microsecond:
        raise CTraderError("cTrader M1 candle range must align to whole minutes")
    if start < datetime(1970, 1, 1, tzinfo=UTC) or end > datetime(
        2038, 1, 19, 3, 14, 6, tzinfo=UTC
    ):
        raise CTraderError("cTrader candle range is outside the supported timestamp range")
    if start >= end:
        raise CTraderError("cTrader candle range start must be earlier than end")
    if end - start > max_duration:
        if max_duration == MAX_CANDLE_REQUEST_INTERVAL:
            raise CTraderError("cTrader M1 requests are limited to a 24-hour UTC interval")
        raise CTraderError(f"cTrader M1 requests are limited to {max_duration.days} days")
    return start, end


def _candle_timestamp(candle: Mapping) -> datetime:
    value = candle.get("opened_at")
    if not isinstance(value, str):
        raise CTraderError(
            "cTrader returned a candle with a malformed timestamp (stage=historical_trendbars)"
        )
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CTraderError(
            "cTrader returned a candle with a malformed timestamp (stage=historical_trendbars)"
        ) from error
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise CTraderError(
            "cTrader returned a candle with a non-UTC timestamp (stage=historical_trendbars)"
        )
    return timestamp.astimezone(UTC)


def _verify_account_authorization(response: Mapping, selected_id: int) -> None:
    authenticated_account_id = response.get("ctidTraderAccountId")
    if authenticated_account_id is None:
        raise CTraderError(
            "cTrader account authorization response is missing its account ID "
            "(stage=account_auth, payload_type=2103, "
            "account_id_present=false, symbols_request_sent=false)"
        )
    try:
        authenticated_account_id = _account_id(authenticated_account_id)
    except CTraderError as error:
        raise CTraderError(
            "cTrader account authorization response has an invalid account ID "
            "(stage=account_auth, payload_type=2103, "
            "account_id_valid=false, symbols_request_sent=false)"
        ) from error
    if authenticated_account_id != selected_id:
        raise CTraderError(
            "cTrader account authorization response did not match "
            "the selected demo account "
            "(stage=account_auth, payload_type=2103, "
            "account_id_match=false, symbols_request_sent=false)"
        )


def _symbol_digits(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 10:
        raise CTraderError("cTrader EURUSD symbol metadata has invalid price precision")
    return value


def _protocol_integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CTraderError(
            "cTrader trendbar contains a malformed numeric field "
            f"(stage=historical_trendbars, field={field}, expected_type=int)"
        )
    return value


def _decode_m1_trendbars(
    response: Mapping,
    start: datetime,
    end: datetime,
    digits: int,
    *,
    diagnostics: bool = False,
    instrument: InstrumentCode = InstrumentCode.EURUSD,
    broker_symbol: str | None = None,
    broker_symbol_id: int | None = None,
) -> list[dict]:
    account_id = response.get("ctidTraderAccountId")
    if account_id is None:
        raise CTraderError(
            "cTrader trendbar response is missing its account ID "
            "(stage=historical_trendbars, payload_type=2138)"
        )
    _account_id(account_id)
    period = response.get("period")
    if isinstance(period, bool) or period not in (Timeframe.M1.value, "M1"):
        raise CTraderError(
            "cTrader returned a trendbar response for the wrong period "
            "(stage=historical_trendbars, expected_period=M1)"
        )

    has_more = response.get("hasMore", False)
    if not isinstance(has_more, bool):
        raise CTraderError(
            "cTrader trendbar response has malformed pagination metadata "
            "(stage=historical_trendbars, field=hasMore, expected_type=bool)"
        )
    # count already covers every possible M1 candle in the requested range;
    # hasMore may refer to records outside that bounded range.
    bars = response.get("trendbar", [])
    if not isinstance(bars, list) or any(not isinstance(bar, Mapping) for bar in bars):
        raise CTraderError(
            "cTrader returned malformed trendbar entries "
            "(stage=historical_trendbars, field=trendbar, expected_type=list_of_objects)"
        )

    candles = []
    previous_timestamp = None
    for bar in bars:
        timestamp_minutes = _protocol_integer(
            bar.get("utcTimestampInMinutes"),
            "utcTimestampInMinutes",
        )
        if timestamp_minutes < 0:
            raise CTraderError(
                "cTrader trendbar contains a negative timestamp "
                "(stage=historical_trendbars, field=utcTimestampInMinutes)"
            )
        if not int(start.timestamp()) // 60 <= timestamp_minutes < int(end.timestamp()) // 60:
            _report_trendbar_range_diagnostic(
                diagnostics,
                start,
                end,
                timestamp_minutes,
                len(bars),
                response.get("hasMore"),
            )
            raise CTraderError(
                "cTrader trendbar timestamp is outside the requested interval "
                "(stage=historical_trendbars)"
            )
        opened_at = datetime.fromtimestamp(timestamp_minutes * 60, tz=UTC)
        if previous_timestamp is not None:
            if timestamp_minutes == previous_timestamp:
                raise CTraderError(
                    "cTrader returned duplicate M1 candle timestamps (stage=historical_trendbars)"
                )
            if timestamp_minutes < previous_timestamp:
                raise CTraderError(
                    "cTrader trendbars are not in chronological order (stage=historical_trendbars)"
                )
        previous_timestamp = timestamp_minutes

        low = _protocol_integer(bar.get("low"), "low")
        open_delta = _protocol_integer(bar.get("deltaOpen"), "deltaOpen")
        close_delta = _protocol_integer(bar.get("deltaClose"), "deltaClose")
        high_delta = _protocol_integer(bar.get("deltaHigh"), "deltaHigh")
        volume = _protocol_integer(bar.get("volume"), "volume")
        if low < 0 or volume < 0:
            raise CTraderError(
                "cTrader trendbar contains a negative price or volume (stage=historical_trendbars)"
            )
        precision = Decimal(1).scaleb(-digits)
        prices = {
            "low": Decimal(low) / TRENDBAR_PRICE_SCALE,
            "open": Decimal(low + open_delta) / TRENDBAR_PRICE_SCALE,
            "close": Decimal(low + close_delta) / TRENDBAR_PRICE_SCALE,
            "high": Decimal(low + high_delta) / TRENDBAR_PRICE_SCALE,
        }
        try:
            candle = Candle(
                instrument=instrument,
                timeframe=Timeframe.M1,
                opened_at=opened_at,
                volume=Decimal(volume),
                price_type=PriceType.UNKNOWN,
                quality_metadata={
                    "source": "ctrader_open_api",
                    "volume_units": "raw_protocol_integer",
                    "broker_symbol": broker_symbol,
                    "broker_symbol_id": broker_symbol_id,
                },
                **{key: value.quantize(precision) for key, value in prices.items()},
            )
        except ValueError as error:
            raise CTraderError(
                "cTrader returned an invalid OHLC candle (stage=historical_trendbars)"
            ) from error
        candle_data = candle.model_dump(mode="json")
        candle_data["timeframe"] = Timeframe.M1.label
        candle_data["opened_at"] = opened_at.isoformat().replace("+00:00", "Z")
        candles.append(candle_data)
    return candles


def _report_trendbar_range_diagnostic(
    enabled: bool,
    start: datetime,
    end: datetime,
    offending_timestamp_minutes: int,
    response_count: int,
    has_more: object,
) -> None:
    if not enabled:
        return
    try:
        offending_timestamp = datetime.fromtimestamp(
            offending_timestamp_minutes * 60,
            tz=UTC,
        ).isoformat()
    except (OSError, OverflowError, ValueError):
        offending_timestamp = "unrepresentable"
    safe_has_more = str(has_more).lower() if isinstance(has_more, bool) else "invalid"
    print(
        "cTrader trendbar range diagnostic "
        f"(requested_start={start.isoformat()}, "
        f"requested_end={end.isoformat()}, "
        f"from_timestamp_ms={int(start.timestamp()) * 1000}, "
        f"to_timestamp_ms={int(end.timestamp()) * 1000 - 1}, "
        f"period=M1, count_parameter=omitted, "
        f"requested_minute_slots={int((end - start).total_seconds() // 60)}, "
        f"response_count={response_count}, has_more={safe_has_more}, "
        f"offending_timestamp_minutes={offending_timestamp_minutes}, "
        f"offending_timestamp={offending_timestamp})",
        file=sys.stderr,
    )


def _account_id(value: object) -> int:
    if isinstance(value, bool):
        raise CTraderError("cTrader account entry has an invalid account identifier")
    try:
        account_id = int(value)
    except (TypeError, ValueError) as error:
        raise CTraderError("cTrader account entry has an invalid account identifier") from error
    if account_id <= 0:
        raise CTraderError("cTrader account entry has an invalid account identifier")
    return account_id


def _payload_state(envelope: Mapping) -> str:
    if "payload" not in envelope:
        return "absent"
    return f"present:{type(envelope['payload']).__name__}"


def _correlation_status(response_id: object, request_id: str) -> str:
    if response_id is None:
        return "absent"
    return "matched" if response_id == request_id else "mismatched"


def _diagnostic_context(context: Mapping[str, bool] | None) -> str:
    if not context:
        return ""
    fields = ",".join(
        f"{name}={str(value).lower()}"
        for name, value in sorted(context.items())
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) and isinstance(value, bool)
    )
    return f", {fields}" if fields else ""


def _heartbeat_message() -> dict:
    return {
        "clientMsgId": str(uuid.uuid4()),
        "payloadType": HEARTBEAT_EVENT,
        "payload": {},
    }


def _close_metadata(error: websockets.ConnectionClosed) -> str:
    received = error.rcvd
    sent = error.sent
    reason_present = bool(
        (received is not None and received.reason) or (sent is not None and sent.reason)
    )
    close_frames = (
        f"exception_class={type(error).__name__}",
        f"close_frame_received={str(received is not None).lower()}",
        f"close_frame_sent={str(sent is not None).lower()}",
        f"received_close_code={received.code if received is not None else 'none'}",
        f"sent_close_code={sent.code if sent is not None else 'none'}",
        f"close_reason_present={str(reason_present).lower()}",
        f"received_then_sent={error.rcvd_then_sent}",
    )
    return ", ".join(close_frames)


def _envelope_metadata(envelope: Mapping) -> str:
    field_types = ",".join(
        f"{key}:{type(value).__name__}"
        for key, value in sorted(envelope.items(), key=lambda item: str(item[0]))
    )
    return f"envelope_field_types={field_types}"


def _demo_account_items(payload: Mapping) -> list[Mapping]:
    account_items = payload.get("ctidTraderAccount")
    if account_items is None:
        documented_ids = payload.get("ctidTraderAccountId")
        if documented_ids is None:
            return []
        if not isinstance(documented_ids, list):
            raise CTraderError(
                "cTrader account-list response has malformed account IDs "
                "(payload_type=2150, field=ctidTraderAccountId, expected_json_type=list)"
            )
        if not documented_ids:
            return []
        raise CTraderError(
            "cTrader account-list response contains IDs without demo/live flags; "
            "refusing to classify accounts as demo "
            "(payload_type=2150, field=ctidTraderAccountId, entries="
            f"{len(documented_ids)})"
        )
    if not isinstance(account_items, list) or any(
        not isinstance(account, Mapping) for account in account_items
    ):
        raise CTraderError(
            "cTrader account-list response has malformed account entries "
            "(payload_type=2150, field=ctidTraderAccount, expected_json_type=list_of_objects)"
        )
    for account in account_items:
        if "ctidTraderAccountId" not in account or not isinstance(account.get("isLive"), bool):
            raise CTraderError(
                "cTrader account-list entry is missing a valid environment flag or account ID "
                "(payload_type=2150, required_fields=ctidTraderAccountId,isLive)"
            )
        _account_id(account["ctidTraderAccountId"])
    return account_items
