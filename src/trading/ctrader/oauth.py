import getpass
import hmac
import importlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from trading.ctrader import encrypted_store

AUTHORIZATION_URL = "https://id.ctrader.com/my/settings/openapi/grantingaccess/"
TOKEN_URL = "https://openapi.ctrader.com/apps/token"
REDIRECT_URI = "http://localhost:8080/callback"
OAUTH_SCOPE = "accounts"
TOKEN_REFRESH_MARGIN_SECONDS = 60


class OAuthError(Exception):
    """An OAuth failure with no sensitive response content."""


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: int

    def usable(self, now: int | None = None) -> bool:
        return (
            self.expires_at
            > (int(time.time()) if now is None else now) + TOKEN_REFRESH_MARGIN_SECONDS
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
            }
        )

    @classmethod
    def from_json(cls, value: str, *, strict: bool = False) -> "OAuthTokens":
        try:
            payload = json.loads(value)
            if not isinstance(payload, dict):
                raise ValueError
            access_token = payload["access_token"]
            refresh_token = payload["refresh_token"]
            raw_expiry = payload["expires_at"]
            if strict and (type(raw_expiry) is not int or raw_expiry <= 0):
                raise ValueError
            expires_at = int(raw_expiry)
            if not isinstance(access_token, str) or not access_token:
                raise ValueError
            if not isinstance(refresh_token, str) or not refresh_token:
                raise ValueError
            if strict and (not access_token.strip() or not refresh_token.strip()):
                raise ValueError
            return cls(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=expires_at,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OAuthError("Stored cTrader token data is invalid") from error


class CTraderSecretStore:
    """Stores cTrader credentials and tokens in the operating system credential vault."""

    SERVICE = "AI-Trading-System-cTrader"
    CLIENT_ID_KEY = "client-id"
    CLIENT_SECRET_KEY = "client-secret"
    TOKEN_KEY = "oauth-tokens"

    def __init__(self, backend=None) -> None:
        if backend is None:
            try:
                backend = encrypted_store.backend_from_environment()
            except encrypted_store.EncryptedStoreError as error:
                raise OAuthError(str(error)) from error
        if backend is None:
            try:
                backend = importlib.import_module("keyring")
            except ImportError as error:
                raise OAuthError(
                    'Install the cTrader extra with: pip install -e ".[ctrader]"'
                ) from error
        self._backend = backend

    def _description(self) -> str:
        if isinstance(self._backend, encrypted_store.EncryptedFileBackend):
            return "encrypted credential file"
        return "Windows credential store"

    def get(self, key: str) -> str | None:
        try:
            return self._backend.get_password(self.SERVICE, key)
        except encrypted_store.EncryptedStoreError as error:
            raise OAuthError(str(error)) from error
        except Exception as error:
            raise OAuthError(f"Unable to read the {self._description()}") from error

    def set(self, key: str, value: str) -> None:
        try:
            self._backend.set_password(self.SERVICE, key, value)
        except encrypted_store.EncryptedStoreError as error:
            raise OAuthError(str(error)) from error
        except Exception as error:
            raise OAuthError(f"Unable to write the {self._description()}") from error


def build_authorization_url(client_id: str, state: str) -> str:
    if not client_id.strip() or not state:
        raise OAuthError("cTrader client credentials are not configured")
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": OAUTH_SCOPE,
            "product": "web",
            "response_type": "code",
            "state": state,
        }
    )
    return f"{AUTHORIZATION_URL}?{query}"


def parse_callback_url(callback_url: str, expected_state: str) -> str:
    try:
        parsed = urlsplit(callback_url)
        query = parse_qs(parsed.query, strict_parsing=True)
        if (parsed.scheme, parsed.netloc, parsed.path, parsed.fragment) != (
            "http",
            "localhost:8080",
            "/callback",
            "",
        ):
            raise OAuthError("OAuth callback URL does not match the configured redirect")
        state_values = query.get("state", [])
        if len(state_values) != 1 or not hmac.compare_digest(state_values[0], expected_state):
            raise OAuthError("OAuth state validation failed")
        if query.get("error"):
            raise OAuthError("cTrader rejected the OAuth authorization request")
        code_values = query.get("code", [])
        if len(code_values) != 1 or not code_values[0]:
            raise OAuthError("OAuth callback did not contain an authorization code")
        return code_values[0]
    except (ValueError, KeyError) as error:
        raise OAuthError("OAuth callback URL is invalid") from error


def receive_authorization_code(
    state: str,
    timeout: int = 120,
    server_factory: Callable = HTTPServer,
) -> str:
    server, result, received = _create_callback_server(state, server_factory)
    deadline = time.monotonic() + timeout
    try:
        while not received.is_set() and time.monotonic() < deadline:
            server.timeout = min(0.5, max(0.01, deadline - time.monotonic()))
            server.handle_request()
    finally:
        server.server_close()
    if not result:
        raise TimeoutError("OAuth callback was not received")
    return result["code"]


def _post_form(url: str, data: dict[str, str], timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode("ascii"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
        raise OAuthError("cTrader OAuth request failed") from error
    if not isinstance(payload, dict) or "error" in payload:
        raise OAuthError("cTrader rejected the OAuth request")
    return payload


class OAuthManager:
    def __init__(
        self,
        store: CTraderSecretStore,
        post_form: Callable = _post_form,
        now: Callable[[], int] = lambda: int(time.time()),
    ) -> None:
        self.store = store
        self._post_form = post_form
        self._now = now

    def credentials(self) -> tuple[str, str]:
        client_id = self.store.get(CTraderSecretStore.CLIENT_ID_KEY)
        client_secret = self.store.get(CTraderSecretStore.CLIENT_SECRET_KEY)
        if not client_id or not client_secret:
            raise OAuthError("cTrader credentials are not configured; run `ctrader configure`")
        return client_id, client_secret

    def validate_stored_configuration(self) -> None:
        """Check worker prerequisites locally, without refreshing tokens or changing storage."""
        client_id, client_secret = self.credentials()
        if not client_id.strip() or not client_secret.strip():
            raise OAuthError("Stored cTrader client credentials are invalid")
        stored = self.store.get(CTraderSecretStore.TOKEN_KEY)
        if not stored:
            raise OAuthError("No cTrader authorization is stored; run `ctrader auth`")
        # An expired access token is valid configuration when refresh data is present.
        OAuthTokens.from_json(stored, strict=True)

    def configure(self, client_id: str, client_secret: str) -> None:
        if not client_id.strip() or not client_secret:
            raise OAuthError("cTrader client ID and client secret are required")
        self.store.set(CTraderSecretStore.CLIENT_ID_KEY, client_id.strip())
        self.store.set(CTraderSecretStore.CLIENT_SECRET_KEY, client_secret)

    def authorize(self, code: str) -> OAuthTokens:
        client_id, client_secret = self.credentials()
        payload = self._post_form(
            TOKEN_URL,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            10,
        )
        tokens = self._tokens_from_response(payload)
        self.store.set(CTraderSecretStore.TOKEN_KEY, tokens.to_json())
        return tokens

    def access_token(self) -> str:
        self.credentials()
        stored = self.store.get(CTraderSecretStore.TOKEN_KEY)
        if not stored:
            raise OAuthError("No cTrader authorization is stored; run `ctrader auth`")
        tokens = OAuthTokens.from_json(stored)
        if tokens.usable(self._now()):
            return tokens.access_token
        client_id, client_secret = self.credentials()
        payload = self._post_form(
            TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            10,
        )
        refreshed = self._tokens_from_response(payload, previous_refresh_token=tokens.refresh_token)
        self.store.set(CTraderSecretStore.TOKEN_KEY, refreshed.to_json())
        return refreshed.access_token

    def _tokens_from_response(
        self,
        payload: dict,
        previous_refresh_token: str | None = None,
    ) -> OAuthTokens:
        if "error" in payload:
            raise OAuthError("cTrader rejected the OAuth request")
        try:
            access_token = payload["accessToken"]
            refresh_token = payload.get("refreshToken", previous_refresh_token)
            expires_in = int(payload["expiresIn"])
            if (
                not isinstance(access_token, str)
                or not access_token
                or not isinstance(refresh_token, str)
                or not refresh_token
                or expires_in <= 0
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise OAuthError("cTrader returned an invalid token response") from error
        return OAuthTokens(access_token, refresh_token, self._now() + expires_in)


def run_browser_authorization(client_id: str) -> str:
    state = secrets.token_urlsafe(32)
    authorization_url = build_authorization_url(client_id, state)
    try:
        server, result, received = _create_callback_server(state)
    except OSError:
        _open_authorization_page(authorization_url)
        print("The local callback listener is unavailable. Complete authorization in a browser,")
        print("then paste the full redirect URL here; its code will not be displayed.")
        return parse_callback_url(getpass.getpass("Callback URL: "), state)
    thread = threading.Thread(
        target=_serve_callback_server,
        args=(server, received, 120),
        daemon=True,
    )
    thread.start()
    _open_authorization_page(authorization_url)
    received.wait(120)
    received.set()
    thread.join(1)
    server.server_close()
    if not result:
        print("The callback was not received. Paste the full redirect URL to continue.")
        return parse_callback_url(getpass.getpass("Callback URL: "), state)
    return result["code"]


def _open_authorization_page(authorization_url: str) -> None:
    try:
        opened = webbrowser.open(authorization_url)
    except (OSError, webbrowser.Error):
        opened = False
    if not opened:
        print(f"Open this cTrader authorization URL in a browser:\n{authorization_url}")


def _create_callback_server(state: str, server_factory: Callable = HTTPServer):
    result: dict[str, str] = {}
    received = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                result["code"] = parse_callback_url(f"http://localhost:8080{self.path}", state)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Authorization received. Return to the terminal.")
                received.set()
            except OAuthError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid callback. Return to the terminal.")

        def log_message(self, _format: str, *_args) -> None:
            return

    server = server_factory(("127.0.0.1", 8080), CallbackHandler)
    return server, result, received


def _serve_callback_server(server: HTTPServer, received: threading.Event, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while not received.is_set() and time.monotonic() < deadline:
        server.timeout = min(0.5, max(0.01, deadline - time.monotonic()))
        server.handle_request()
