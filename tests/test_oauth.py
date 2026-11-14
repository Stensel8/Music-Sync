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

from musicsync.errors import LoginError, NetworkError, NotLoggedIn
from musicsync.oauth import (
    ClientCredentials,
    OAuthClient,
    StoredToken,
    Token,
    TokenStore,
    _CallbackHandler,
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


@pytest.mark.skipif(os.name == "nt", reason="Windows has no such permissions")
@pytest.mark.parametrize("leftover", ["tokens.json", "tokens.tmp"])
def test_the_token_file_is_private_whatever_was_there_before(tmp_path, leftover):
    (tmp_path / leftover).write_text("{}", encoding="utf-8")
    (tmp_path / leftover).chmod(0o644)  # readable by everyone
    store = TokenStore(tmp_path / "tokens.json")
    store.save("demo", Token("a", "r"))
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert [p.name for p in tmp_path.iterdir() if p.name != leftover] in ([], ["tokens.json"])  # no temp file stays


def test_two_logins_saved_at_once_are_both_kept(tmp_path):
    # A job refreshing one login while another is saved: the file is read, changed and written back, and
    # the two must not do that through each other.
    reading, go_on = threading.Event(), threading.Event()

    class SlowStore(TokenStore):
        def _read(self):
            data = super()._read()
            if threading.current_thread().name == "slow":
                reading.set()
                go_on.wait(5)  # hold what was read, as a slow disk would
            return data

    store = SlowStore(tmp_path / "tokens.json")
    slow = threading.Thread(target=store.save, args=("spotify", Token("s")), name="slow")
    fast = threading.Thread(target=store.save, args=("tidal", Token("t")), name="fast")
    slow.start()
    assert reading.wait(5)
    fast.start()
    fast.join(0.2)  # long enough to write, were nothing stopping it
    go_on.set()
    slow.join(5)
    fast.join(5)
    assert store.load("spotify") == Token("s") and store.load("tidal") == Token("t")


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


@responses.activate
@pytest.mark.parametrize(
    "answer",
    [
        {"body": "<html>Down for maintenance</html>"},
        {"json": {"token_type": "Bearer"}},
        {"json": ["access_token"]},
        {"json": {"access_token": "A", "expires_in": "soon"}},
    ],
)
def test_a_token_answer_that_makes_no_sense_is_a_login_error(answer):
    responses.post(TOKEN_URL, **answer)
    with pytest.raises(LoginError, match="usable token"):
        demo().exchange("CODE", "V")


@responses.activate
def test_a_token_answer_without_an_expiry_gets_an_hour():
    responses.post(TOKEN_URL, json={"access_token": "A", "expires_in": None, "scope": None})
    token = demo().exchange("CODE", "V")
    assert 3500 < token.expires_at - time.time() <= 3600 and token.scope == ""


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
def test_a_login_server_in_trouble_is_not_an_expired_session(tmp_path):
    # A 503 says nothing about the login: "log in again" would be the wrong advice, and would not help.
    store = TokenStore(tmp_path / "t.json")
    store.save("demo", Token("old", "R", 0))
    responses.post(TOKEN_URL, status=503)
    with pytest.raises(NetworkError, match="HTTP 503"):
        StoredToken(demo(), store)()
    assert store.load("demo") == Token("old", "R", 0)


@responses.activate
def test_client_credentials_are_cached():
    responses.post(TOKEN_URL, json={"access_token": "APP", "expires_in": 1000})
    provider = ClientCredentials(demo("S"))
    assert provider() == provider() == "APP"
    assert len(responses.calls) == 1


# --- the loopback login (real sockets, so `responses` must stay out of it) --------------------------


def run_login(query: str, state: str = "STATE", host: str = "127.0.0.1", idle_first: bool = False) -> dict[str, object]:
    """Run the loopback login on a thread, then hit its redirect URI the way a browser would."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as probe:
        probe.bind((host, 0))
        port = probe.getsockname()[1]
    address = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    redirect = f"http://{address}/demo/callback"
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
            requests.get(f"http://{address}/favicon.ico", timeout=1)  # a browser asks for this too
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    # A browser opens spare connections and may leave one unused: it says nothing, and stays open.
    idle = socket.create_connection((host, port)) if idle_first else None
    try:
        requests.get(f"{redirect}?{query}", timeout=5)
    finally:
        if idle:
            idle.close()
    thread.join(timeout=10)
    return outcome


def test_loopback_login_returns_the_code():
    assert run_login("code=abc&state=STATE") == {"code": "abc"}


def test_loopback_login_rejects_a_wrong_state():
    assert "state mismatch" in str(run_login("code=abc&state=OTHER")["error"])


def test_loopback_login_reports_a_refusal():
    assert "access_denied" in str(run_login("error=access_denied&state=STATE")["error"])


def test_loopback_login_is_not_held_up_by_a_connection_that_says_nothing(monkeypatch):
    assert _CallbackHandler.timeout, "a connection that says nothing has to time out"
    monkeypatch.setattr(_CallbackHandler, "timeout", 0.2)  # the test need not wait as long as a browser may take
    assert run_login("code=abc&state=STATE", idle_first=True) == {"code": "abc"}


def test_loopback_login_works_on_the_ipv6_loopback():
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        pytest.skip("no IPv6 loopback here")
    assert run_login("code=abc&state=STATE", host="::1") == {"code": "abc"}


@pytest.mark.parametrize("redirect", ["https://example.com/cb", "http://127.0.0.1:99999/cb", "http://127.0.0.1:x/cb"])
def test_loopback_login_only_accepts_loopback_redirects(redirect):
    with pytest.raises(LoginError, match="loopback"):
        loopback_login("https://auth.test/authorize", redirect, "S", open_browser=False)
