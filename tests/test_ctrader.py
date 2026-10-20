import argparse
import asyncio
import json
import urllib.parse
from datetime import UTC, datetime, timedelta

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from trading.ctrader.cli import _parse_utc_datetime, _parser
from trading.ctrader.client import (
    ACCOUNT_AUTH,
    ACCOUNT_LIST,
    APPLICATION_AUTH,
    BASE_CANDLE_RETRY_DELAY_SECONDS,
    DEFAULT_MAX_MESSAGE_SIZE_BYTES,
    ERROR_RESPONSE,
    HEARTBEAT_EVENT,
    HEARTBEAT_INTERVAL_SECONDS,
    MAX_ALLOWED_MESSAGE_SIZE_BYTES,
    MAX_CANDLE_CHUNK_ATTEMPTS,
    MAX_MULTI_DAY_CANDLE_DURATION,
    OPEN_API_ERROR_RESPONSE,
    READ_ONLY_MESSAGES,
    SYMBOL_BY_ID,
    SYMBOLS_LIST,
    TRADER,
    TRENDBARS,
    UNSOLICITED_EVENT_TYPES,
    CTraderError,
    CTraderReadOnlyClient,
    _close_metadata,
    _Connection,
    _heartbeat_message,
    _RetryableCTraderError,
)
from trading.ctrader.oauth import (
    CTraderSecretStore,
    OAuthError,
    OAuthManager,
    OAuthTokens,
    _post_form,
    build_authorization_url,
    parse_callback_url,
)


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, _service, key):
        return self.values.get(key)

    def set_password(self, _service, key, value):
        self.values[key] = value

    def delete_password(self, _service, key):
        self.values.pop(key, None)


class FakeWebSocket:
    def __init__(self, responses):
        self.responses = responses
        self.sent = []

    async def send(self, message):
        request = json.loads(message)
        self.sent.append(request)
        payload_type = request["payloadType"]
        response_type = {
            APPLICATION_AUTH[0]: APPLICATION_AUTH[1],
            ACCOUNT_LIST[0]: ACCOUNT_LIST[1],
            ACCOUNT_AUTH[0]: ACCOUNT_AUTH[1],
            TRADER[0]: TRADER[1],
            SYMBOLS_LIST[0]: SYMBOLS_LIST[1],
            SYMBOL_BY_ID[0]: SYMBOL_BY_ID[1],
            TRENDBARS[0]: TRENDBARS[1],
        }[payload_type]
        payload = self.responses[payload_type]
        if isinstance(payload, Exception):
            raise payload
        self.next_response = {
            "clientMsgId": request["clientMsgId"],
            "payloadType": response_type,
            "payload": payload,
        }

    async def recv(self):
        return json.dumps(self.next_response)


class FakeConnect:
    def __init__(self, websocket):
        self.websocket = websocket
        self.urls = []
        self.options = []

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        self.options.append(kwargs)
        return self

    async def __aenter__(self):
        return self.websocket

    async def __aexit__(self, *_args):
        return False


def _responses(accounts=None, symbols=None):
    demo_accounts = [account for account in (accounts or []) if account.get("isLive") is False]
    account_id = demo_accounts[0]["ctidTraderAccountId"] if demo_accounts else 22
    return {
        APPLICATION_AUTH[0]: {},
        ACCOUNT_LIST[0]: {"ctidTraderAccount": accounts or []},
        ACCOUNT_AUTH[0]: {"ctidTraderAccountId": account_id},
        TRADER[0]: {"trader": {"brokerName": "Demo Broker", "traderLogin": 123}},
        SYMBOLS_LIST[0]: {"symbol": symbols or []},
        SYMBOL_BY_ID[0]: {"symbol": [{"digits": 5, "pipPosition": 4}]},
        TRENDBARS[0]: {
            "ctidTraderAccountId": account_id,
            "period": 1,
            "symbolId": 77,
            "trendbar": [],
        },
    }


def _client(responses, *, timeout=0.01, connect=None):
    websocket = FakeWebSocket(responses)
    connector = connect or FakeConnect(websocket)
    return (
        CTraderReadOnlyClient(
            "client-id",
            "client-secret",
            "access-token",
            connect=connector,
            request_timeout=timeout,
            sleep=_no_sleep,
        ),
        websocket,
        connector,
    )


async def _no_sleep(_duration):
    return None


def test_oauth_state_is_required_and_scope_is_accounts_only():
    url = build_authorization_url("client-id", "expected-state")
    params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert params["scope"] == ["accounts"]
    assert params["redirect_uri"] == ["http://localhost:8080/callback"]
    assert (
        parse_callback_url(
            "http://localhost:8080/callback?code=one-time&state=expected-state",
            "expected-state",
        )
        == "one-time"
    )
    with pytest.raises(OAuthError, match="state"):
        parse_callback_url(
            "http://localhost:8080/callback?code=one-time&state=wrong",
            "expected-state",
        )
    with pytest.raises(OAuthError, match="rejected"):
        parse_callback_url(
            "http://localhost:8080/callback?error=access_denied&state=expected-state",
            "expected-state",
        )


def test_missing_credentials_are_rejected():
    manager = OAuthManager(CTraderSecretStore(MemoryKeyring()))
    with pytest.raises(OAuthError, match="not configured"):
        manager.credentials()


def test_expired_access_token_is_refreshed_and_saved():
    backend = MemoryKeyring()
    store = CTraderSecretStore(backend)
    manager = OAuthManager(store, now=lambda: 100)
    manager.configure("id", "secret")
    store.set(CTraderSecretStore.TOKEN_KEY, OAuthTokens("old", "refresh", 99).to_json())
    calls = []

    def refresh(_url, form, _timeout):
        calls.append(form)
        return {"accessToken": "new", "expiresIn": 3600}

    manager._post_form = refresh
    assert manager.access_token() == "new"
    assert calls[0]["grant_type"] == "refresh_token"
    assert OAuthTokens.from_json(store.get(CTraderSecretStore.TOKEN_KEY)).access_token == "new"


def test_rejected_authorization_does_not_store_token():
    store = CTraderSecretStore(MemoryKeyring())
    manager = OAuthManager(store)
    manager.configure("id", "secret")
    manager._post_form = lambda *_args: {"error": "invalid_grant"}
    with pytest.raises(OAuthError):
        manager.authorize("one-time-code")
    assert store.get(CTraderSecretStore.TOKEN_KEY) is None


def test_authorization_tokens_are_not_rendered_in_repr():
    rendered = repr(OAuthTokens("access-secret", "refresh-secret", 10_000))
    assert "access-secret" not in rendered
    assert "refresh-secret" not in rendered


def test_demo_account_filtering_never_returns_live_account():
    responses = _responses(
        accounts=[
            {"ctidTraderAccountId": 1, "isLive": True},
            {"ctidTraderAccountId": 2, "isLive": False},
        ]
    )
    client, _, connector = _client(responses)
    accounts = asyncio.run(client.list_demo_accounts())
    assert accounts == [{"account_id": 2, "is_live": False}]
    assert connector.urls == ["wss://demo.ctraderapi.com:5036"]


def test_connection_uses_configured_bounded_message_size():
    websocket = FakeWebSocket(_responses())
    connector = FakeConnect(websocket)
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=connector,
        max_message_size_bytes=6 * 1024 * 1024,
    )
    assert (
        CTraderReadOnlyClient("id", "secret", "token")._max_message_size_bytes
        == DEFAULT_MAX_MESSAGE_SIZE_BYTES
    )

    async def open_and_close():
        async with client._connection():
            return None

    asyncio.run(open_and_close())
    assert connector.options[0]["max_size"] == 6 * 1024 * 1024
    assert connector.options[0]["max_size"] <= MAX_ALLOWED_MESSAGE_SIZE_BYTES
    assert connector.urls == ["wss://demo.ctraderapi.com:5036"]


@pytest.mark.parametrize("size", [0, -1, MAX_ALLOWED_MESSAGE_SIZE_BYTES + 1, True, "8 MiB"])
def test_connection_rejects_invalid_or_unbounded_message_size(size):
    with pytest.raises(ValueError, match="max_message_size_bytes"):
        CTraderReadOnlyClient(
            "id",
            "secret",
            "token",
            max_message_size_bytes=size,
        )


@pytest.mark.parametrize("payload_shape", ["absent", "null"])
def test_application_auth_ack_accepts_payloadless_documented_response(payload_shape):
    class PayloadlessAuthSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            self.sent.append(request)
            if request["payloadType"] != APPLICATION_AUTH[0]:
                await super().send(message)
                return
            self.next_response = {
                "clientMsgId": request["clientMsgId"],
                "payloadType": APPLICATION_AUTH[1],
            }
            if payload_shape == "null":
                self.next_response["payload"] = None

    websocket = PayloadlessAuthSocket(
        _responses(accounts=[{"ctidTraderAccountId": 42, "isLive": False}])
    )
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=FakeConnect(websocket),
        sleep=_no_sleep,
    )
    assert asyncio.run(client.list_demo_accounts()) == [{"account_id": 42, "is_live": False}]


@pytest.mark.parametrize("payload_shape", ["absent", "null"])
def test_account_list_response_still_requires_payload_object(payload_shape):
    class PayloadlessAccountListSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            self.sent.append(request)
            if request["payloadType"] != ACCOUNT_LIST[0]:
                await super().send(message)
                return
            self.next_response = {
                "clientMsgId": request["clientMsgId"],
                "payloadType": ACCOUNT_LIST[1],
            }
            if payload_shape == "null":
                self.next_response["payload"] = None

    websocket = PayloadlessAccountListSocket(_responses())
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=FakeConnect(websocket),
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.list_demo_accounts())
    assert "expected_payload_type=2150" in str(error.value)
    assert f"payload_state={'absent' if payload_shape == 'absent' else 'present:NoneType'}" in str(
        error.value
    )
    assert "envelope_field_types=" in str(error.value)
    assert "token" not in str(error.value)
    assert "secret" not in str(error.value)


def test_live_account_is_rejected_without_fallback():
    with pytest.raises(CTraderError, match="No demo"):
        CTraderReadOnlyClient._select_demo_account(
            [{"ctidTraderAccountId": 1, "isLive": True}],
            None,
        )
    with pytest.raises(CTraderError, match="not an accessible demo"):
        CTraderReadOnlyClient._select_demo_account(
            [{"ctidTraderAccountId": 2, "isLive": False}],
            1,
        )


def test_eurusd_metadata_is_scoped_to_selected_demo_account():
    responses = _responses(
        accounts=[{"ctidTraderAccountId": "22", "isLive": False}],
        symbols=[{"symbolId": 5, "symbolName": "EUR/USD"}],
    )
    client, websocket, _ = _client(responses)
    result = asyncio.run(client.discover_eurusd())
    assert result["account"]["account_id"] == 22
    assert result["symbol"] == {
        "internal_instrument": "EURUSD",
        "broker_symbol_name": "EUR/USD",
        "broker_symbol_id": 5,
        "account_id": 22,
        "environment": "demo",
        "digits": 5,
        "pip_position": 4,
    }
    assert [request["payloadType"] for request in websocket.sent] == [
        APPLICATION_AUTH[0],
        ACCOUNT_LIST[0],
        ACCOUNT_AUTH[0],
        TRADER[0],
        SYMBOLS_LIST[0],
        SYMBOL_BY_ID[0],
    ]
    assert websocket.sent[0]["payload"] == {
        "clientId": "client-id",
        "clientSecret": "client-secret",
    }
    assert websocket.sent[1]["payload"] == {"accessToken": "access-token"}
    assert websocket.sent[2]["payload"] == {
        "ctidTraderAccountId": 22,
        "accessToken": "access-token",
    }
    assert websocket.sent[3]["payload"] == {"ctidTraderAccountId": 22}
    assert websocket.sent[4]["payload"] == {
        "ctidTraderAccountId": 22,
        "includeArchivedSymbols": False,
    }
    assert websocket.sent[5]["payload"] == {
        "ctidTraderAccountId": 22,
        "symbolId": [5],
    }
    assert all(
        isinstance(request["payload"]["ctidTraderAccountId"], int) for request in websocket.sent[2:]
    )


@pytest.mark.parametrize(
    ("account_auth_payload", "expected_message"),
    [
        ({}, "account_id_present=false"),
        ({"ctidTraderAccountId": 99}, "account_id_match=false"),
    ],
)
def test_symbols_request_is_not_sent_until_account_auth_is_verified(
    account_auth_payload,
    expected_message,
):
    responses = _responses(
        accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
        symbols=[{"symbolId": 5, "symbolName": "EURUSD"}],
    )
    responses[ACCOUNT_AUTH[0]] = account_auth_payload
    client, websocket, _ = _client(responses)
    with pytest.raises(CTraderError, match=expected_message) as error:
        asyncio.run(client.discover_eurusd())
    request_types = [request["payloadType"] for request in websocket.sent]
    assert ACCOUNT_AUTH[0] in request_types
    assert SYMBOLS_LIST[0] not in request_types
    assert "symbols_request_sent=false" in str(error.value)


def test_symbols_discovery_ignores_unrelated_async_event_and_matches_response():
    class EventBeforeSymbolsSocket(FakeWebSocket):
        def __init__(self, responses):
            self.responses = responses
            self.sent = []
            self.symbol_event = None

        async def send(self, message):
            await super().send(message)
            request = self.sent[-1]
            if request["payloadType"] == SYMBOLS_LIST[0]:
                self.symbol_event = self.next_response
                self.next_response = {
                    "payloadType": 2123,
                    "payload": {"ctidTraderAccountId": 22},
                }

        async def recv(self):
            if self.symbol_event is not None:
                event = self.next_response
                self.next_response = self.symbol_event
                self.symbol_event = None
                return json.dumps(event)
            return await super().recv()

    websocket = EventBeforeSymbolsSocket(
        _responses(
            accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
            symbols=[{"symbolId": 5, "symbolName": "EURUSD"}],
        )
    )
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=FakeConnect(websocket),
        sleep=_no_sleep,
    )
    result = asyncio.run(client.discover_eurusd())
    assert result["symbol"]["broker_symbol_name"] == "EURUSD"
    assert websocket.sent[-1]["payloadType"] == SYMBOL_BY_ID[0]


def test_account_disconnect_event_reports_symbol_stage_without_payload_values():
    class AccountDisconnectSocket(FakeWebSocket):
        def __init__(self, responses):
            super().__init__(responses)
            self.symbol_response = None

        async def send(self, message):
            await super().send(message)
            if self.sent[-1]["payloadType"] == SYMBOLS_LIST[0]:
                self.symbol_response = self.next_response
                self.next_response = {
                    "payloadType": 2164,
                    "payload": {
                        "ctidTraderAccountId": 22,
                        "reason": "sensitive server diagnostic",
                    },
                }

        async def recv(self):
            if self.symbol_response is not None:
                event = self.next_response
                self.next_response = self.symbol_response
                self.symbol_response = None
                return json.dumps(event)
            return await super().recv()

    websocket = AccountDisconnectSocket(
        _responses(
            accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
            symbols=[{"symbolId": 5, "symbolName": "EURUSD"}],
        )
    )
    client = CTraderReadOnlyClient(
        "id", "secret", "token", connect=FakeConnect(websocket), sleep=_no_sleep
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.discover_eurusd())
    assert "stage=symbols_list" in str(error.value)
    assert "event_payload_type=2164" in str(error.value)
    assert "account_disconnected" in str(error.value)
    assert "sensitive server diagnostic" not in str(error.value)


def test_symbols_timeout_reports_stage_and_safe_last_message_type():
    class MismatchedSymbolReplySocket(FakeWebSocket):
        def __init__(self, responses):
            self.responses = responses
            self.sent = []
            self.wait_for_timeout = False
            self.mismatch_pending = False

        async def send(self, message):
            request = json.loads(message)
            if request["payloadType"] != SYMBOLS_LIST[0]:
                await super().send(message)
                return
            self.sent.append(request)
            self.next_response = {
                "clientMsgId": "another-request",
                "payloadType": SYMBOLS_LIST[1],
                "payload": {"symbol": []},
            }
            self.mismatch_pending = True

        async def recv(self):
            if self.mismatch_pending:
                self.mismatch_pending = False
                return await super().recv()
            if self.sent and self.sent[-1]["payloadType"] == SYMBOLS_LIST[0]:
                self.wait_for_timeout = True
            if self.wait_for_timeout:
                await asyncio.sleep(1)
            return await super().recv()

    websocket = MismatchedSymbolReplySocket(
        _responses(
            accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
            symbols=[],
        )
    )
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=FakeConnect(websocket),
        request_timeout=0.001,
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.discover_eurusd())
    assert "stage=symbols_list" in str(error.value)
    assert "request_payload_type=2114" in str(error.value)
    assert "expected_payload_type=2115" in str(error.value)
    assert "last_response_payload_type=2115" in str(error.value)
    assert "last_response_correlation=mismatched" in str(error.value)
    assert "another-request" not in str(error.value)
    assert "secret" not in str(error.value)
    assert "token" not in str(error.value)


def test_symbols_connection_close_reports_closed_not_timed_out():
    class ClosingSymbolsSocket(FakeWebSocket):
        async def recv(self):
            if self.sent and self.sent[-1]["payloadType"] == SYMBOLS_LIST[0]:
                raise ConnectionClosedError(None, None)
            return await super().recv()

    websocket = ClosingSymbolsSocket(
        _responses(
            accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
            symbols=[],
        )
    )
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=FakeConnect(websocket),
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.discover_eurusd())
    assert "connection closed while waiting" in str(error.value)
    assert "stage=symbols_list" in str(error.value)
    assert "request_send_completed=true" in str(error.value)
    assert "account_auth_completed=true" in str(error.value)
    assert "account_id_verified=true" in str(error.value)
    assert "exception_class=ConnectionClosedError" in str(error.value)
    assert "close_frame_received=false" in str(error.value)
    assert "close_frame_sent=false" in str(error.value)
    assert "received_close_code=none" in str(error.value)
    assert "sent_close_code=none" in str(error.value)
    assert "received_then_sent=None" in str(error.value)
    assert "timed out" not in str(error.value)


def test_oversized_symbols_response_fails_safely_with_close_1009():
    class OversizedSymbolsSocket(FakeWebSocket):
        async def recv(self):
            if self.sent and self.sent[-1]["payloadType"] == SYMBOLS_LIST[0]:
                raise ConnectionClosedError(
                    None,
                    Close(1009, "response exceeded receive limit"),
                )
            return await super().recv()

    websocket = OversizedSymbolsSocket(
        _responses(
            accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
            symbols=[],
        )
    )
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "access-token",
        connect=FakeConnect(websocket),
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.discover_eurusd())
    message = str(error.value)
    assert "stage=symbols_list" in message
    assert "exception_class=ConnectionClosedError" in message
    assert "close_frame_received=false" in message
    assert "close_frame_sent=true" in message
    assert "sent_close_code=1009" in message
    assert "response exceeded receive limit" not in message
    assert "access-token" not in message


def test_heartbeat_uses_required_json_envelope_fields():
    message = _heartbeat_message()
    assert message["payloadType"] == HEARTBEAT_EVENT
    assert message["payload"] == {}
    assert isinstance(message["clientMsgId"], str)
    assert message["clientMsgId"]
    assert set(message) == {"clientMsgId", "payloadType", "payload"}
    assert isinstance(HEARTBEAT_INTERVAL_SECONDS, int)
    assert HEARTBEAT_INTERVAL_SECONDS == 10


def test_connection_heartbeat_sends_documented_json_envelope(monkeypatch):
    sent = []
    sleep_calls = []

    async def fake_sleep(delay):
        sleep_calls.append(delay)

    class CancelAfterHeartbeat:
        async def send(self, message):
            sent.append(json.loads(message))
            raise asyncio.CancelledError

    client = type("Client", (), {"_heartbeat_error": None})()
    connection = _Connection(
        connect=None,
        timeout=1,
        attempts=1,
        sleep=fake_sleep,
        send_lock=asyncio.Lock(),
        client=client,
    )
    connection._websocket = CancelAfterHeartbeat()
    monkeypatch.setattr("trading.ctrader.client.asyncio.sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(connection._heartbeat())
    assert sleep_calls == [HEARTBEAT_INTERVAL_SECONDS]
    assert len(sent) == 1
    assert sent[0]["payloadType"] == HEARTBEAT_EVENT
    assert sent[0]["payload"] == {}
    assert sent[0]["clientMsgId"]
    assert set(sent[0]) == {"clientMsgId", "payloadType", "payload"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ConnectionClosedError(None, Close(4002, "sensitive close reason")),
            (
                "exception_class=ConnectionClosedError",
                "close_frame_received=false",
                "close_frame_sent=true",
                "received_close_code=none",
                "sent_close_code=4002",
                "close_reason_present=true",
                "received_then_sent=None",
            ),
        ),
        (
            ConnectionClosedError(Close(4003, "another private reason"), None),
            (
                "exception_class=ConnectionClosedError",
                "close_frame_received=true",
                "close_frame_sent=false",
                "received_close_code=4003",
                "sent_close_code=none",
                "close_reason_present=true",
                "received_then_sent=None",
            ),
        ),
    ],
)
def test_close_metadata_reports_frame_side_codes_without_reason_text(error, expected):
    metadata = _close_metadata(error)
    for item in expected:
        assert item in metadata
    assert "sensitive close reason" not in metadata
    assert "another private reason" not in metadata


def test_account_auth_protocol_error_prevents_symbols_request():
    class AccountAuthErrorSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            if request["payloadType"] != ACCOUNT_AUTH[0]:
                await super().send(message)
                return
            self.sent.append(request)
            self.next_response = {
                "clientMsgId": request["clientMsgId"],
                "payloadType": OPEN_API_ERROR_RESPONSE,
                "payload": {
                    "errorCode": "ACCOUNT_NOT_AUTHORIZED",
                    "description": "sensitive details",
                },
            }

    websocket = AccountAuthErrorSocket(
        _responses(accounts=[{"ctidTraderAccountId": 22, "isLive": False}])
    )
    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=FakeConnect(websocket),
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.discover_eurusd())
    assert "stage=account_auth" in str(error.value)
    assert "payload_type=2142" in str(error.value)
    assert "sensitive" not in str(error.value)
    assert SYMBOLS_LIST[0] not in [request["payloadType"] for request in websocket.sent]


def test_missing_eurusd_symbol_is_reported():
    client, _, _ = _client(_responses(accounts=[{"ctidTraderAccountId": 22, "isLive": False}]))
    with pytest.raises(CTraderError, match="EURUSD was not found"):
        asyncio.run(client.discover_eurusd())


def test_timeout_is_reported_without_exposing_request_payload():
    class HangingSocket(FakeWebSocket):
        async def send(self, _message):
            return None

        async def recv(self):
            await asyncio.sleep(1)

    websocket = HangingSocket({})
    client = CTraderReadOnlyClient(
        "client-id",
        "client-secret",
        "access-token",
        connect=FakeConnect(websocket),
        request_timeout=0.001,
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError, match="timed out"):
        asyncio.run(client.list_demo_accounts())


def test_api_errors_are_sanitized():
    class ErrorSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            self.next_response = {
                "clientMsgId": request["clientMsgId"],
                "payloadType": ERROR_RESPONSE,
                "payload": {"description": "token=do-not-leak"},
            }

    client = CTraderReadOnlyClient(
        "id", "secret", "access-token", connect=FakeConnect(ErrorSocket({}))
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.list_demo_accounts())
    assert "do-not-leak" not in str(error.value)


@pytest.mark.parametrize("error_type", [ERROR_RESPONSE, OPEN_API_ERROR_RESPONSE])
def test_documented_protocol_error_responses_include_safe_metadata(error_type):
    class ErrorSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            self.next_response = {
                "clientMsgId": request["clientMsgId"],
                "payloadType": error_type,
                "payload": {
                    "errorCode": "INVALID_REQUEST",
                    "description": "token=must-not-appear",
                },
            }

    client = CTraderReadOnlyClient(
        "id", "secret", "access-token", connect=FakeConnect(ErrorSocket({}))
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.list_demo_accounts())
    assert f"payload_type={error_type}" in str(error.value)
    assert "error_code=INVALID_REQUEST" in str(error.value)
    assert "client_msg_id=matched" in str(error.value)
    assert "must-not-appear" not in str(error.value)


def test_documented_account_id_only_response_fails_closed_with_protocol_diagnostics():
    responses = _responses()
    responses[ACCOUNT_LIST[0]] = {"ctidTraderAccountId": [123456]}
    client, _, _ = _client(responses)
    with pytest.raises(CTraderError, match="without demo/live flags") as error:
        asyncio.run(client.list_demo_accounts())
    assert "payload_type=2150" in str(error.value)
    assert "entries=1" in str(error.value)
    assert "123456" not in str(error.value)


def test_empty_documented_account_list_is_handled_as_empty():
    responses = _responses()
    responses[ACCOUNT_LIST[0]] = {"ctidTraderAccountId": []}
    client, _, _ = _client(responses)
    assert asyncio.run(client.list_demo_accounts()) == []


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"payloadType": 2101, "payload": ["not", "an", "object"]}, "payload_state=present:list"),
        ([], "received_json_type=list"),
        ({"clientMsgId": "request", "payloadType": "2101", "payload": {}}, "invalid payloadType"),
    ],
)
def test_malformed_wire_responses_report_only_safe_metadata(response, message):
    class MalformedSocket(FakeWebSocket):
        async def send(self, _message):
            self.next_response = response

    client = CTraderReadOnlyClient(
        "id", "secret", "access-token", connect=FakeConnect(MalformedSocket({}))
    )
    with pytest.raises(CTraderError, match=message) as error:
        asyncio.run(client.list_demo_accounts())
    assert "client-secret" not in str(error.value)
    assert "access-token" not in str(error.value)
    assert "not an object" not in str(error.value)


def test_invalid_json_message_reports_expected_payload_type_only():
    class InvalidJsonSocket(FakeWebSocket):
        async def send(self, _message):
            return None

        async def recv(self):
            return '{"payloadType":'

    client = CTraderReadOnlyClient(
        "id", "secret", "access-token", connect=FakeConnect(InvalidJsonSocket({}))
    )
    with pytest.raises(CTraderError, match="invalid JSON") as error:
        asyncio.run(client.list_demo_accounts())
    assert "expected_payload_type=2101" in str(error.value)
    assert 'payloadType":' not in str(error.value)


def test_response_without_client_message_id_is_accepted_by_expected_type():
    class NoCorrelationSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            response_type = {
                APPLICATION_AUTH[0]: APPLICATION_AUTH[1],
                ACCOUNT_LIST[0]: ACCOUNT_LIST[1],
            }[request["payloadType"]]
            self.next_response = {
                "payloadType": response_type,
                "payload": self.responses[request["payloadType"]],
            }

    websocket = NoCorrelationSocket(_responses(accounts=[]))
    client = CTraderReadOnlyClient("id", "secret", "access-token", connect=FakeConnect(websocket))
    assert asyncio.run(client.list_demo_accounts()) == []


def test_mismatched_client_message_id_is_not_treated_as_the_response():
    class MismatchedThenHangingSocket(FakeWebSocket):
        def __init__(self):
            self.received = False

        async def send(self, _message):
            return None

        async def recv(self):
            if not self.received:
                self.received = True
                return json.dumps(
                    {
                        "clientMsgId": "different-request",
                        "payloadType": APPLICATION_AUTH[1],
                        "payload": {},
                    }
                )
            await asyncio.sleep(1)

    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "access-token",
        connect=FakeConnect(MismatchedThenHangingSocket()),
        request_timeout=0.001,
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.list_demo_accounts())
    assert "expected_payload_type=2101" in str(error.value)
    assert "last_response_payload_type=2101" in str(error.value)
    assert "last_response_correlation=mismatched" in str(error.value)
    assert "different-request" not in str(error.value)


def test_connection_failure_uses_bounded_retries_and_is_sanitized():
    attempts = []

    def broken_connect(*_args, **_kwargs):
        attempts.append(1)
        raise OSError("credential-looking diagnostic")

    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "access-token",
        connect=broken_connect,
        connection_attempts=2,
        sleep=_no_sleep,
    )
    with pytest.raises(CTraderError, match="Unable to connect") as error:
        asyncio.run(client.list_demo_accounts())
    assert len(attempts) == 2
    assert "credential-looking" not in str(error.value)


def test_oauth_network_timeout_is_sanitized(monkeypatch):
    def timeout(*_args, **_kwargs):
        raise TimeoutError("sensitive request details")

    monkeypatch.setattr("urllib.request.urlopen", timeout)
    with pytest.raises(OAuthError, match="request failed") as error:
        _post_form("https://openapi.ctrader.com/apps/token", {"code": "secret"}, 1)
    assert "sensitive" not in str(error.value)


def test_read_only_client_exposes_no_order_or_position_operations():
    names = dir(CTraderReadOnlyClient)
    assert not any("order" in name.lower() or "position" in name.lower() for name in names)


def test_read_only_request_allowlist_rejects_unrecognized_protocol_messages():
    client, websocket, _ = _client(_responses())
    with pytest.raises(CTraderError, match="outside the read-only"):
        asyncio.run(client._request(websocket, 2104, 2105, {}))
    assert websocket.sent == []


def test_order_request_payload_type_is_not_treated_as_an_unsolicited_event():
    class OrderRequestSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            self.next_response = {
                "clientMsgId": request["clientMsgId"],
                "payloadType": 2110,
                "payload": {},
            }

    websocket = OrderRequestSocket({})
    client = CTraderReadOnlyClient(
        "id", "secret", "token", connect=FakeConnect(websocket), sleep=_no_sleep
    )
    assert 2110 not in UNSOLICITED_EVENT_TYPES
    with pytest.raises(CTraderError, match="unexpected response"):
        asyncio.run(
            client._request(
                websocket,
                APPLICATION_AUTH[0],
                APPLICATION_AUTH[1],
                {},
            )
        )


def _trendbar(
    timestamp_minutes,
    *,
    low=110000,
    open_delta=100,
    close_delta=300,
    high_delta=500,
    volume=23,
):
    return {
        "utcTimestampInMinutes": timestamp_minutes,
        "low": low,
        "deltaOpen": open_delta,
        "deltaClose": close_delta,
        "deltaHigh": high_delta,
        "volume": volume,
    }


def _candle_client(trendbars, *, has_more=None):
    responses = _responses(
        accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
        symbols=[{"symbolId": 77, "symbolName": "EURUSD"}],
    )
    responses[TRENDBARS[0]]["trendbar"] = trendbars
    if has_more is not None:
        responses[TRENDBARS[0]]["hasMore"] = has_more
    return _client(responses)


def test_historical_m1_request_uses_discovered_demo_account_and_symbol():
    start = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    end = datetime(2026, 1, 2, 3, 6, tzinfo=UTC)
    start_minutes = int(start.timestamp()) // 60
    client, websocket, connector = _candle_client(
        [_trendbar(start_minutes), _trendbar(start_minutes + 1)]
    )

    candles = asyncio.run(client.get_eurusd_m1_candles(start, end))

    request = next(message for message in websocket.sent if message["payloadType"] == TRENDBARS[0])
    assert request["payload"] == {
        "ctidTraderAccountId": 22,
        "fromTimestamp": int(start.timestamp()) * 1000,
        "toTimestamp": int(end.timestamp()) * 1000 - 1,
        "period": 1,
        "symbolId": 77,
    }
    assert connector.urls == ["wss://demo.ctraderapi.com:5036"] * 2
    assert [candle["opened_at"] for candle in candles] == [
        "2026-01-02T03:04:00Z",
        "2026-01-02T03:05:00Z",
    ]
    assert candles[0]["open"] == "1.10100"
    assert candles[0]["close"] == "1.10300"
    assert candles[0]["high"] == "1.10500"
    assert candles[0]["low"] == "1.10000"
    assert candles[0]["volume"] == "23"
    assert candles[0]["quality_metadata"]["volume_units"] == "raw_protocol_integer"


def test_historical_m1_empty_result_returns_empty_list():
    start = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    end = datetime(2026, 1, 2, 3, 5, tzinfo=UTC)
    client, _, _ = _candle_client([])
    assert asyncio.run(client.get_eurusd_m1_candles(start, end)) == []


@pytest.mark.parametrize("duration_hours", [18, 23, 24])
def test_historical_m1_accepts_start_and_last_minute_of_longer_intervals(duration_hours):
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = start + timedelta(hours=duration_hours)
    start_minutes = int(start.timestamp()) // 60
    final_minute = int(end.timestamp()) // 60 - 1
    client, websocket, _ = _candle_client([_trendbar(start_minutes), _trendbar(final_minute)])

    candles = asyncio.run(client.get_eurusd_m1_candles(start, end))

    assert [candle["opened_at"] for candle in candles] == [
        start.isoformat().replace("+00:00", "Z"),
        (end - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    ]
    request = next(message for message in websocket.sent if message["payloadType"] == TRENDBARS[0])
    assert request["payload"]["fromTimestamp"] == int(start.timestamp()) * 1000
    assert request["payload"]["toTimestamp"] == int(end.timestamp()) * 1000 - 1
    assert "count" not in request["payload"]


def test_historical_m1_rejects_end_boundary_and_reports_safe_diagnostics(capsys):
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = start + timedelta(hours=23)
    offending_timestamp = int(end.timestamp()) // 60
    client, _, _ = _candle_client([_trendbar(offending_timestamp)])

    with pytest.raises(CTraderError, match="outside the requested interval"):
        asyncio.run(client.get_eurusd_m1_candles(start, end, diagnostics=True))

    output = capsys.readouterr()
    assert output.out == ""
    assert "requested_start=2026-01-05T00:00:00+00:00" in output.err
    assert "requested_end=2026-01-05T23:00:00+00:00" in output.err
    assert "response_count=1" in output.err
    assert "requested_minute_slots=1380" in output.err
    assert "count_parameter=omitted" in output.err
    assert "offending_timestamp=2026-01-05T23:00:00+00:00" in output.err
    assert "account_id" not in output.err
    assert "client-secret" not in output.err


def test_historical_m1_does_not_use_count_that_can_pull_bars_before_from_timestamp(capsys):
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = start + timedelta(hours=23)
    first_returned_minute = int((start - timedelta(minutes=13)).timestamp()) // 60
    response_bars = [_trendbar(first_returned_minute + offset) for offset in range(1380)]
    client, websocket, _ = _candle_client(response_bars, has_more=True)

    with pytest.raises(CTraderError, match="outside the requested interval"):
        asyncio.run(client.get_eurusd_m1_candles(start, end, diagnostics=True))

    request = next(message for message in websocket.sent if message["payloadType"] == TRENDBARS[0])
    assert request["payload"] == {
        "ctidTraderAccountId": 22,
        "fromTimestamp": 1767571200000,
        "toTimestamp": 1767653999999,
        "period": 1,
        "symbolId": 77,
    }
    assert "count" not in request["payload"]
    diagnostic = capsys.readouterr().err
    assert "response_count=1380" in diagnostic
    assert "offending_timestamp=2026-01-04T23:47:00+00:00" in diagnostic


def test_historical_m1_range_failure_is_silent_without_diagnostics(capsys):
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = start + timedelta(hours=23)
    client, _, _ = _candle_client([_trendbar(int(end.timestamp()) // 60)])

    with pytest.raises(CTraderError, match="outside the requested interval"):
        asyncio.run(client.get_eurusd_m1_candles(start, end))

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == ""


def test_historical_m1_accepts_has_more_for_a_full_requested_interval():
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 5, 0, 5, tzinfo=UTC)
    start_minutes = int(start.timestamp()) // 60
    client, websocket, _ = _candle_client(
        [_trendbar(start_minutes + offset) for offset in range(5)],
        has_more=True,
    )

    candles = asyncio.run(client.get_eurusd_m1_candles(start, end))

    assert len(candles) == 5
    request = next(message for message in websocket.sent if message["payloadType"] == TRENDBARS[0])
    assert "count" not in request["payload"]


def test_historical_m1_accepts_partial_response_and_market_gaps_with_has_more():
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 5, 0, 5, tzinfo=UTC)
    start_minutes = int(start.timestamp()) // 60
    client, websocket, _ = _candle_client(
        [_trendbar(start_minutes), _trendbar(start_minutes + 2), _trendbar(start_minutes + 4)],
        has_more=True,
    )

    candles = asyncio.run(client.get_eurusd_m1_candles(start, end))

    assert [candle["opened_at"] for candle in candles] == [
        "2026-01-05T00:00:00Z",
        "2026-01-05T00:02:00Z",
        "2026-01-05T00:04:00Z",
    ]
    request = next(message for message in websocket.sent if message["payloadType"] == TRENDBARS[0])
    assert "count" not in request["payload"]


@pytest.mark.parametrize(
    ("bars", "error"),
    [
        ([_trendbar(100, high_delta=100)], "invalid OHLC"),
        ([_trendbar(100), _trendbar(100)], "duplicate"),
        ([_trendbar(101), _trendbar(100)], "chronological"),
        ([_trendbar(100, volume=-1)], "negative price or volume"),
    ],
)
def test_malformed_historical_m1_candles_are_rejected(bars, error):
    start = datetime.fromtimestamp(100 * 60, tz=UTC)
    end = datetime.fromtimestamp(103 * 60, tz=UTC)
    client, _, _ = _candle_client(bars, has_more=True)
    with pytest.raises(CTraderError, match=error):
        asyncio.run(client.get_eurusd_m1_candles(start, end))


def test_historical_m1_rejects_non_boolean_has_more_metadata():
    start = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    end = datetime(2026, 1, 5, 0, 1, tzinfo=UTC)
    client, _, _ = _candle_client([], has_more="true")
    with pytest.raises(CTraderError, match="pagination metadata"):
        asyncio.run(client.get_eurusd_m1_candles(start, end))


@pytest.mark.parametrize(
    ("start", "end", "error"),
    [
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC), "earlier"),
        (
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, 0, 1, tzinfo=UTC),
            "24-hour",
        ),
        (
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            "whole minutes",
        ),
        (
            datetime(2026, 1, 1, tzinfo=None),
            datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
            "UTC",
        ),
    ],
)
def test_historical_m1_range_requires_a_short_aligned_utc_interval(start, end, error):
    client, _, _ = _candle_client([])
    with pytest.raises(CTraderError, match=error):
        asyncio.run(client.get_eurusd_m1_candles(start, end))


def test_historical_m1_api_error_is_reported_with_stage_without_payload_values():
    class TrendbarErrorSocket(FakeWebSocket):
        async def send(self, message):
            request = json.loads(message)
            if request["payloadType"] != TRENDBARS[0]:
                await super().send(message)
                return
            self.sent.append(request)
            self.next_response = {
                "clientMsgId": request["clientMsgId"],
                "payloadType": OPEN_API_ERROR_RESPONSE,
                "payload": {
                    "errorCode": "NOT_AUTHORIZED",
                    "description": "sensitive token text",
                },
            }

    responses = _responses(
        accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
        symbols=[{"symbolId": 77, "symbolName": "EURUSD"}],
    )
    websocket = TrendbarErrorSocket(responses)
    connector = FakeConnect(websocket)
    client = CTraderReadOnlyClient("id", "secret", "token", connect=connector, sleep=_no_sleep)
    start = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    end = datetime(2026, 1, 2, 3, 5, tzinfo=UTC)
    with pytest.raises(CTraderError) as error:
        asyncio.run(client.get_eurusd_m1_candles(start, end))
    assert "stage=historical_trendbars" in str(error.value)
    assert "NOT_AUTHORIZED" in str(error.value)
    assert "sensitive token text" not in str(error.value)


def test_candles_cli_requires_explicit_demo_instrument_and_utc_interval():
    args = _parser().parse_args(
        [
            "candles",
            "--instrument",
            "EURUSD",
            "--environment",
            "demo",
            "--start",
            "2026-01-02T03:04:00Z",
            "--end",
            "2026-01-02T03:05:00Z",
        ]
    )
    assert args.command == "candles"
    assert args.start == datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_utc_datetime("2026-01-02T03:04:00")


def _multi_day_candle(timestamp):
    return {"opened_at": timestamp.isoformat().replace("+00:00", "Z")}


def test_multi_day_candles_use_consecutive_non_overlapping_24_hour_chunks():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=2, minutes=5)
    requests = []
    client, _, _ = _client(_responses())

    async def retrieve(chunk_start, chunk_end, account_id=None, *, diagnostics=False):
        requests.append((chunk_start, chunk_end, account_id))
        if chunk_start == start + timedelta(days=2):
            return [_multi_day_candle(chunk_start)]
        return []

    client.get_eurusd_m1_candles = retrieve
    candles = asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end, 22))

    assert requests == [
        (start, start + timedelta(days=1), 22),
        (start + timedelta(days=1), start + timedelta(days=2), 22),
        (start + timedelta(days=2), end, 22),
    ]
    assert candles == [_multi_day_candle(start + timedelta(days=2))]


def test_multi_day_candles_allow_weekend_gaps_without_synthesizing_bars():
    start = datetime(2026, 1, 2, 23, 59, tzinfo=UTC)
    end = datetime(2026, 1, 5, 0, 2, tzinfo=UTC)
    expected = [
        _multi_day_candle(start),
        _multi_day_candle(datetime(2026, 1, 5, 0, 1, tzinfo=UTC)),
    ]
    calls = []
    client, _, _ = _client(_responses())

    async def retrieve(chunk_start, _chunk_end, _account_id=None, *, diagnostics=False):
        calls.append(chunk_start)
        if chunk_start == start:
            return [expected[0]]
        if chunk_start == datetime(2026, 1, 4, 23, 59, tzinfo=UTC):
            return expected[1:]
        return []

    client.get_eurusd_m1_candles = retrieve
    candles = asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end))

    assert calls == [
        start,
        start + timedelta(days=1),
        start + timedelta(days=2),
    ]
    assert candles == expected


def test_multi_day_candles_preserve_exact_24_hour_chunk_boundaries():
    start = datetime(2026, 1, 5, tzinfo=UTC)
    end = start + timedelta(hours=48)
    chunk_requests = []
    expected = []
    client, _, _ = _client(_responses())

    async def retrieve(chunk_start, chunk_end, _account_id=None, *, diagnostics=False):
        chunk_requests.append((chunk_start, chunk_end))
        candles = [
            _multi_day_candle(chunk_start),
            _multi_day_candle(chunk_end - timedelta(minutes=1)),
        ]
        expected.extend(candles)
        return candles

    client.get_eurusd_m1_candles = retrieve
    candles = asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end))

    assert chunk_requests == [
        (start, start + timedelta(days=1)),
        (start + timedelta(days=1), end),
    ]
    assert [candle["opened_at"] for candle in candles] == [
        "2026-01-05T00:00:00Z",
        "2026-01-05T23:59:00Z",
        "2026-01-06T00:00:00Z",
        "2026-01-06T23:59:00Z",
    ]
    assert candles == expected


def test_multi_day_candles_reject_duplicate_timestamps():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    duplicate = _multi_day_candle(start)
    client, _, _ = _client(_responses())

    async def retrieve(_chunk_start, _chunk_end, _account_id=None, *, diagnostics=False):
        return [duplicate, duplicate]

    client.get_eurusd_m1_candles = retrieve
    with pytest.raises(CTraderError, match="duplicate candles"):
        asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end))


def test_multi_day_candles_reject_out_of_order_results():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    client, _, _ = _client(_responses())

    async def retrieve(_chunk_start, _chunk_end, _account_id=None, *, diagnostics=False):
        return [
            _multi_day_candle(start + timedelta(minutes=1)),
            _multi_day_candle(start),
        ]

    client.get_eurusd_m1_candles = retrieve
    with pytest.raises(CTraderError, match="chronological order"):
        asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end))


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [
        (
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
            "earlier",
        ),
        (
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC) + MAX_MULTI_DAY_CANDLE_DURATION + timedelta(minutes=1),
            "31 days",
        ),
        (
            datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            "whole minutes",
        ),
        (
            datetime(2026, 1, 1, tzinfo=None),
            datetime(2026, 1, 2, tzinfo=UTC),
            "UTC",
        ),
    ],
)
def test_multi_day_candles_reject_invalid_ranges(start, end, message):
    client, _, _ = _client(_responses())
    with pytest.raises(CTraderError, match=message):
        asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end))


def test_multi_day_candles_retry_transient_failures_with_bounded_backoff(capsys):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    calls = []
    delays = []
    client, _, _ = _client(_responses())

    async def record_sleep(delay):
        delays.append(delay)

    async def retrieve(_chunk_start, _chunk_end, _account_id=None, *, diagnostics=False):
        calls.append(None)
        if len(calls) < 3:
            raise _RetryableCTraderError("temporary transport error")
        return []

    client._sleep = record_sleep
    client.get_eurusd_m1_candles = retrieve
    assert asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end)) == []
    assert len(calls) == 3
    assert delays == [
        BASE_CANDLE_RETRY_DELAY_SECONDS,
        BASE_CANDLE_RETRY_DELAY_SECONDS * 2,
    ]
    assert "retrying historical candle chunk" in capsys.readouterr().err


def test_multi_day_candle_request_observer_counts_only_historical_attempts():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    client, websocket, _ = _client(
        _responses(
            accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
            symbols=[{"symbolName": "EURUSD", "symbolId": 77}],
        )
    )
    attempts = []

    candles = asyncio.run(
        client.get_eurusd_m1_candles_multi_day(
            start,
            end,
            request_observer=lambda: attempts.append("trendbar"),
        )
    )

    assert candles == []
    assert attempts == ["trendbar"]
    assert all(request["payloadType"] in READ_ONLY_MESSAGES for request in websocket.sent)
    assert [request["payloadType"] for request in websocket.sent].count(TRENDBARS[0]) == 1


def test_multi_day_retry_observer_counts_each_bounded_retry():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    client, _, _ = _client(_responses())
    requests = []
    observed_attempts = []

    async def retrieve(
        _chunk_start,
        _chunk_end,
        _account_id=None,
        *,
        diagnostics=False,
        request_observer=None,
    ):
        requests.append(None)
        request_observer()
        if len(requests) < 3:
            raise _RetryableCTraderError("temporary transport error")
        return []

    client.get_eurusd_m1_candles = retrieve
    result = asyncio.run(
        client.get_eurusd_m1_candles_multi_day(
            start,
            end,
            request_observer=lambda: observed_attempts.append("trendbar"),
        )
    )

    assert result == []
    assert len(requests) == len(observed_attempts) == 3


def test_multi_day_candles_stop_after_retry_exhaustion(capsys):
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    calls = []
    delays = []
    client, _, _ = _client(_responses())

    async def record_sleep(delay):
        delays.append(delay)

    async def retrieve(_chunk_start, _chunk_end, _account_id=None, *, diagnostics=False):
        calls.append(None)
        raise _RetryableCTraderError("temporary transport error")

    client._sleep = record_sleep
    client.get_eurusd_m1_candles = retrieve
    with pytest.raises(_RetryableCTraderError):
        asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end))
    assert len(calls) == MAX_CANDLE_CHUNK_ATTEMPTS
    assert len(delays) == MAX_CANDLE_CHUNK_ATTEMPTS - 1
    assert "attempt=3/3" in capsys.readouterr().err


def test_multi_day_candles_do_not_retry_non_transient_errors():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=1)
    calls = []
    client, _, _ = _client(_responses())

    async def retrieve(_chunk_start, _chunk_end, _account_id=None, *, diagnostics=False):
        calls.append(None)
        raise CTraderError("invalid request")

    client.get_eurusd_m1_candles = retrieve
    with pytest.raises(CTraderError, match="invalid request"):
        asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end))
    assert len(calls) == 1


def test_rate_limit_response_retries_after_documented_retry_after(capsys):
    class RateLimitedSocket(FakeWebSocket):
        def __init__(self, responses):
            super().__init__(responses)
            self.trendbar_requests = 0

        async def send(self, message):
            request = json.loads(message)
            if request["payloadType"] == TRENDBARS[0]:
                self.sent.append(request)
                self.trendbar_requests += 1
                if self.trendbar_requests == 1:
                    self.next_response = {
                        "clientMsgId": request["clientMsgId"],
                        "payloadType": OPEN_API_ERROR_RESPONSE,
                        "payload": {
                            "errorCode": "BLOCKED_PAYLOAD_TYPE",
                            "retryAfter": 3,
                        },
                    }
                    return
            await super().send(message)

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=1)
    responses = _responses(
        accounts=[{"ctidTraderAccountId": 22, "isLive": False}],
        symbols=[{"symbolId": 77, "symbolName": "EURUSD"}],
    )
    websocket = RateLimitedSocket(responses)
    delays = []

    async def record_sleep(delay):
        delays.append(delay)

    client = CTraderReadOnlyClient(
        "id",
        "secret",
        "token",
        connect=FakeConnect(websocket),
        sleep=record_sleep,
    )
    assert asyncio.run(client.get_eurusd_m1_candles_multi_day(start, end)) == []
    assert websocket.trendbar_requests == 2
    assert delays == [3]
    assert "retrying historical candle chunk" in capsys.readouterr().err


def test_cli_multi_day_flag_keeps_json_on_stdout_and_messages_on_stderr(
    monkeypatch,
    capsys,
):
    class FakeManager:
        def __init__(self, _store):
            pass

        def credentials(self):
            return "client-id", "client-secret"

        def access_token(self):
            return "access-token"

    class FakeClient:
        diagnostics_values = []

        def __init__(self, *_args):
            pass

        async def get_eurusd_m1_candles_multi_day(self, *_args, **_kwargs):
            self.diagnostics_values.append(_kwargs.get("diagnostics"))
            return [{"opened_at": "2026-01-01T00:00:00Z"}]

    from trading.ctrader import cli

    monkeypatch.setattr(cli, "OAuthManager", FakeManager)
    monkeypatch.setattr(cli, "CTraderReadOnlyClient", FakeClient)
    exit_code = cli.main(
        [
            "candles",
            "--instrument",
            "EURUSD",
            "--environment",
            "demo",
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-02T00:00:00Z",
            "--multi-day",
            "--diagnostics",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == [{"opened_at": "2026-01-01T00:00:00Z"}]
    assert captured.err == ""
    assert FakeClient.diagnostics_values == [True]
