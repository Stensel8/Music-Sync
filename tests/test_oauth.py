import base64
import hashlib
import os
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
import responses

from musicsync.errors import LoginError, NotLoggedIn
from musicsync.oauth import (
    ClientCredentials,
    OAuthClient,
    StoredToken,
    Token,
    TokenStore,
    loopback_login,
    pkce_pair,
)

from .support import sent_form

TOKEN_URL = "https://auth.test/token"


class DemoOAuth(OAuthClient):
    name = "demo"
    authorize_endpoint = "https://auth.test/authorize"
    token_endpoint = TOKEN_URL
    scopes = ("read", "write")


def demo(secret: str = "") -> DemoOAuth:
    return DemoOAuth("cid", "http://127.0.0.1:8888/cb", secret)


# --- PKCE and the authorize URL -----------------------------------------------------------------


def test_the_pkce_challenge_is_the_sha256_of_the_verifier():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert challenge == base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def test_the_authorize_url_carries_pkce_state_and_scopes():
    query = parse_qs(urlsplit(demo().authorize_url("STATE", "CHAL")).query)
    assert query["client_id"] == ["cid"] and query["state"] == ["STATE"]
    assert query["code_challenge"] == ["CHAL"] and query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["read write"] and query["redirect_uri"] == ["http://127.0.0.1:8888/cb"]


# --- the token file -------------------------------------------------------------------------------


def test_token_store_round_trip_and_permissions(tmp_path):
    store = TokenStore(tmp_path / "config" / "tokens.json")  # the folder is created
    assert store.load("demo") is None
    store.save("demo", Token("a", "r", 123.0, "read"))
    assert store.load("demo") == Token("a", "r", 123.0, "read")
    if os.name != "nt":
        assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.delete("demo") is True
    assert store.delete("demo") is False
    assert store.load("demo") is None


@pytest.mark.parametrize("content", ["{not json", "[1, 2]", '{"demo": {"unexpected": 1}}'])
def test_a_damaged_token_file_means_not_logged_in(tmp_path, content):
    path = tmp_path / "tokens.json"
    path.write_text(content, encoding="utf-8")
    assert TokenStore(path).load("demo") is None


# --- talking to the token endpoint ----------------------------------------------------------------


@responses.activate
def test_exchange_sends_the_verifier_and_parses_the_token():
    responses.post(TOKEN_URL, json={"access_token": "A", "refresh_token": "R", "expires_in": 100, "scope": "read"})
    token = demo().exchange("CODE", "VERIFIER")
    form = sent_form(responses.calls[0])
    assert (
        form["grant_type"] == ["authorization_code"]
        and form["code"] == ["CODE"]
        and form["code_verifier"] == ["VERIFIER"]
    )
    assert "client_secret" not in form
    assert (token.access_token, token.refresh_token) == ("A", "R") and not token.expired()


@responses.activate
def test_refresh_keeps_the_old_refresh_token_when_none_is_returned():
    responses.post(TOKEN_URL, json={"access_token": "NEW", "expires_in": 100})
    fresh = demo("S").refresh(Token("OLD", "R", 0))
    assert (fresh.access_token, fresh.refresh_token) == ("NEW", "R")
    assert sent_form(responses.calls[0])["client_secret"] == ["S"]


def test_refresh_without_a_refresh_token_is_a_login_error():
    with pytest.raises(LoginError, match="refresh token"):
        demo().refresh(Token("OLD"))


@responses.activate
def test_client_credentials_use_basic_auth_and_need_a_secret():
    responses.post(TOKEN_URL, json={"access_token": "APP", "expires_in": 100})
    assert demo("S").client_credentials().access_token == "APP"
    assert responses.calls[0].request.headers["Authorization"] == "Basic " + base64.b64encode(b"cid:S").decode()
    with pytest.raises(LoginError, match="secret"):
        demo().client_credentials()


@responses.activate
def test_token_endpoint_errors_become_login_errors():
    responses.post(TOKEN_URL, status=400, json={"error": "invalid_grant", "error_description": "Code expired"})
    with pytest.raises(LoginError, match="Code expired"):
        demo().exchange("CODE", "V")


# --- token providers ---------------------------------------------------------------------------------


@responses.activate
def test_an_expired_token_is_refreshed_and_saved(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    provider = StoredToken(demo(), store)
    with pytest.raises(NotLoggedIn):
        provider()
    store.save("demo", Token("old", "R", time.time() - 10))
    responses.post(TOKEN_URL, json={"access_token": "new", "expires_in": 100})
    assert provider() == "new"
    saved = store.load("demo")
    assert saved is not None and saved.access_token == "new"
    assert provider() == "new" and len(responses.calls) == 1  # still valid: no second refresh


def test_the_token_is_kept_in_memory_not_read_from_disk_on_every_call(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.save("demo", Token("a", "r", time.time() + 1000))
    provider = StoredToken(demo(), store)
    assert provider() == "a"
    store.path.unlink()
    assert provider() == "a"


@responses.activate
def test_a_login_someone_else_just_refreshed_is_not_refreshed_again(tmp_path):
    # Refresh tokens can be single-use, so two jobs must not both spend the same one.
    store = TokenStore(tmp_path / "t.json")
    store.save("demo", Token("old", "R", time.time() + 1000))
    first, second = StoredToken(demo(), store), StoredToken(demo(), store)
    assert first() == second() == "old"
    responses.post(TOKEN_URL, json={"access_token": "new", "refresh_token": "R2", "expires_in": 100})
    assert first(force_refresh=True) == "new"  # the server rejected "old", so this one refreshes
    assert second(force_refresh=True) == "new"  # also rejected "old": takes the token first saved
    assert len(responses.calls) == 1


@responses.activate
def test_a_failed_refresh_asks_for_a_new_login(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    store.save("demo", Token("old", "R", 0))
    responses.post(TOKEN_URL, status=400, json={"error": "invalid_grant"})
    with pytest.raises(NotLoggedIn, match="music-sync login demo"):
        StoredToken(demo(), store)()


@responses.activate
def test_client_credentials_are_cached():
    responses.post(TOKEN_URL, json={"access_token": "APP", "expires_in": 1000})
    provider = ClientCredentials(demo("S"))
    assert provider() == provider() == "APP"
    assert len(responses.calls) == 1


# --- the loopback login (real sockets, so `responses` must stay out of it) --------------------------


def run_login(query: str, state: str = "STATE") -> dict[str, object]:
    """Run the loopback login on a thread, then hit its redirect URI the way a browser would."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    redirect = f"http://127.0.0.1:{port}/demo/callback"
    outcome: dict[str, object] = {}

    def login() -> None:
        try:
            outcome["code"] = loopback_login(
                "https://auth.test/authorize", redirect, state, open_browser=False, out=lambda _: None, timeout=10
            )
        except LoginError as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=login)
    thread.start()
    for _ in range(50):  # wait until the server is listening
        try:
            requests.get(f"http://127.0.0.1:{port}/favicon.ico", timeout=1)  # a browser asks for this too
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    requests.get(f"{redirect}?{query}", timeout=5)
    thread.join(timeout=10)
    return outcome


def test_loopback_login_returns_the_code():
    assert run_login("code=abc&state=STATE") == {"code": "abc"}


def test_loopback_login_rejects_a_wrong_state():
    assert "state mismatch" in str(run_login("code=abc&state=OTHER")["error"])


def test_loopback_login_reports_a_refusal():
    assert "access_denied" in str(run_login("error=access_denied&state=STATE")["error"])


def test_loopback_login_only_accepts_loopback_redirects():
    with pytest.raises(LoginError, match="loopback"):
        loopback_login("https://auth.test/authorize", "https://example.com/cb", "S", open_browser=False)
