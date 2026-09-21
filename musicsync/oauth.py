"""OAuth 2.0 authorization code flow with PKCE, the CLI's loopback login, and token storage."""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from collections.abc import Callable
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from .config import config_dir
from .errors import LoginError, NetworkError, NotLoggedIn
from .http import error_message


@dataclass(slots=True)
class Token:
    access_token: str
    refresh_token: str | None = None
    expires_at: float = 0.0  # unix time
    scope: str = ""

    def expired(self, margin: float = 60) -> bool:
        """True when the token has expired, or will within ``margin`` seconds."""
        return time.time() >= self.expires_at - margin


class TokenStore:
    """One token per service, in a JSON file that only the current user can read."""

    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "tokens.json"
        # Held while a token is refreshed, so two jobs in this process cannot both spend a one-time refresh token.
        self.lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return {}  # missing or corrupt: the same as not being logged in
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        # Created as 0600 from the start, so the tokens are never readable by others, not even briefly.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        tmp.replace(self.path)  # atomic: a crash cannot leave half a file

    def load(self, service: str) -> Token | None:
        try:
            return Token(**self._read()[service])
        except KeyError, TypeError:
            return None

    def save(self, service: str, token: Token) -> None:
        self._write({**self._read(), service: asdict(token)})

    def delete(self, service: str) -> bool:
        data = self._read()
        if data.pop(service, None) is None:
            return False
        self._write(data)
        return True


def pkce_pair() -> tuple[str, str]:
    """A PKCE (code verifier, code challenge) pair (RFC 7636, method S256)."""
    verifier = base64.urlsafe_b64encode(os.urandom(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


# --- the CLI login: catch the redirect on a local port ------------------------------------------

_DONE_PAGE = (
    b"<!doctype html><meta charset=utf-8><title>Music-Sync</title>"
    b"<body style='font-family:sans-serif;text-align:center;margin-top:4rem'>"
    b"<h2>Done</h2><p>You can close this tab and return to the terminal.</p></body>"
)
_LOOPBACK = ("127.0.0.1", "localhost", "::1")


class _CallbackServer(HTTPServer):
    """Waits for the single redirect the browser makes back to us after the user has logged in."""

    def __init__(self, address: tuple[str, int], path: str):
        super().__init__(address, _CallbackHandler)
        self.path = path
        self.params: dict[str, str] = {}
        self.timeout = 1  # handle_request() then returns after a second, so the caller can watch its deadline


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = cast(_CallbackServer, self.server)
        url = urlsplit(self.path)
        if url.path != server.path:
            self.send_error(404)  # for instance the browser asking for /favicon.ico
            return
        server.params = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_DONE_PAGE)))
        self.end_headers()
        self.wfile.write(_DONE_PAGE)

    def log_message(self, format: str, *args: Any) -> None:
        """Stay quiet: the terminal belongs to the CLI."""


def loopback_login(
    authorize_url: str,
    redirect_uri: str,
    expected_state: str,
    *,
    timeout: float = 300,
    open_browser: bool = True,
    out: Callable[[str], None] = print,
) -> str:
    """Send the user to ``authorize_url`` and wait for the redirect on a local port. Returns the code."""
    target = urlsplit(redirect_uri)
    host = target.hostname or ""
    if host not in _LOOPBACK:
        raise LoginError(f"The CLI login needs a loopback redirect URI (127.0.0.1), got {redirect_uri}")
    try:
        server = _CallbackServer((host, target.port or 80), target.path)
    except OSError as exc:
        raise LoginError(
            f"Cannot listen on {host}:{target.port} ({exc}). Is the web interface or another login still running?"
        ) from exc

    with server:  # closes the socket on the way out
        out(f"Opening your browser to log in. If nothing opens, visit:\n{authorize_url}\n")
        if open_browser:
            webbrowser.open(authorize_url)
        deadline = time.monotonic() + timeout
        while not server.params and time.monotonic() < deadline:
            server.handle_request()

    params = server.params
    if not params:
        raise LoginError("Timed out waiting for the login to finish.")
    if "error" in params:
        raise LoginError(f"Login was refused: {params['error']}")
    if params.get("state") != expected_state:
        raise LoginError("Login failed: state mismatch (possible CSRF); please try again.")
    if "code" not in params:
        raise LoginError("Login failed: no authorization code was returned.")
    return params["code"]


# --- talking to a service's OAuth endpoints ---------------------------------------------------------


class OAuthClient:
    """Authorization code flow with PKCE for one service. Subclasses fill in the endpoints and scopes."""

    name = ""
    authorize_endpoint = ""
    token_endpoint = ""
    scopes: tuple[str, ...] = ()

    def __init__(
        self,
        client_id: str,
        redirect_uri: str,
        client_secret: str = "",
        session: requests.Session | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.session = session or requests.Session()

    def authorize_url(self, state: str, challenge: str) -> str:
        """Where to send the user's browser to log in."""
        return f"{self.authorize_endpoint}?" + urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": " ".join(self.scopes),
                "state": state,
                "code_challenge_method": "S256",
                "code_challenge": challenge,
            }
        )

    def _token_request(self, data: dict[str, str], auth: tuple[str, str] | None = None) -> Token:
        try:
            resp = self.session.post(self.token_endpoint, data=data, auth=auth, timeout=20)
        except requests.RequestException as exc:
            raise NetworkError(f"could not reach the {self.name} login server: {exc}") from exc
        if not resp.ok:
            raise LoginError(f"{self.name} token request failed (HTTP {resp.status_code}): {error_message(resp)}")
        payload = resp.json()
        return Token(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=time.time() + float(payload.get("expires_in", 3600)),
            scope=payload.get("scope", ""),
        )

    def _with_secret(self, data: dict[str, str]) -> dict[str, str]:
        return {**data, "client_secret": self.client_secret} if self.client_secret else data

    def exchange(self, code: str, verifier: str) -> Token:
        """Trade the authorization code from the redirect for tokens."""
        return self._token_request(
            self._with_secret(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "code_verifier": verifier,
                }
            )
        )

    def refresh(self, token: Token) -> Token:
        if not token.refresh_token:
            raise LoginError(f"the {self.name} login has no refresh token")
        fresh = self._token_request(
            self._with_secret(
                {"grant_type": "refresh_token", "refresh_token": token.refresh_token, "client_id": self.client_id}
            )
        )
        # A service only sends a new refresh token when it rotates them; otherwise keep the old one.
        fresh.refresh_token = fresh.refresh_token or token.refresh_token
        return fresh

    def client_credentials(self) -> Token:
        """An app-level token (no user), for looking things up in a catalogue."""
        if not self.client_secret:
            raise LoginError(f"{self.name}: app-level access needs a client secret")
        return self._token_request({"grant_type": "client_credentials"}, auth=(self.client_id, self.client_secret))

    def login(self, store: TokenStore, **loopback_options: Any) -> Token:
        """The whole CLI login: browser, redirect, token exchange, saved to ``store``."""
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(16)
        code = loopback_login(self.authorize_url(state, challenge), self.redirect_uri, state, **loopback_options)
        token = self.exchange(code, verifier)
        store.save(self.name, token)
        return token


# --- token providers for ApiClient ----------------------------------------------------------------


class StoredToken:
    """The logged-in user's token. It is kept in memory and refreshed when it has expired."""

    def __init__(self, oauth: OAuthClient, store: TokenStore):
        self.oauth = oauth
        self.store = store
        self._token: Token | None = None

    def __call__(self, force_refresh: bool = False) -> str:
        token = self._token or self.store.load(self.oauth.name)
        if token is None:
            raise NotLoggedIn(f"Not logged in to {self.oauth.name}. Run: music-sync login {self.oauth.name}")
        if force_refresh or token.expired():
            token = self._refresh(token)
        self._token = token
        return token.access_token

    def _refresh(self, stale: Token) -> Token:
        name = self.oauth.name
        expired = NotLoggedIn(f"Your {name} session has expired. Run: music-sync login {name}")
        with self.store.lock:
            # Another job may have refreshed this very login while we waited for the lock.
            current = self.store.load(name)
            if current is None:
                raise expired
            if current.access_token != stale.access_token and not current.expired():
                return current
            try:
                fresh = self.oauth.refresh(current)
            except LoginError as exc:
                raise expired from exc
            self.store.save(name, fresh)
            return fresh


class ClientCredentials:
    """An app-level token (no user), fetched on first use and kept in memory."""

    def __init__(self, oauth: OAuthClient):
        self.oauth = oauth
        self._token: Token | None = None

    def __call__(self, force_refresh: bool = False) -> str:
        if force_refresh or self._token is None or self._token.expired():
            self._token = self.oauth.client_credentials()
        return self._token.access_token
