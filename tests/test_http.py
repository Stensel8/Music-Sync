import pytest
import requests
import responses

from musicsync.errors import ApiError, NetworkError, QuotaExceeded
from musicsync.http import ApiClient

URL = "https://api.test/thing"


def make_client(sleeps: list[float] | None = None, **kwargs) -> ApiClient:
    """A client whose token is "t1", or "t2" once a refresh was asked for, and that never really sleeps."""
    sleep = sleeps.append if sleeps is not None else (lambda _: None)
    return ApiClient("https://api.test", lambda force: "t2" if force else "t1", sleep=sleep, **kwargs)


@responses.activate
def test_sends_the_bearer_token_and_returns_json():
    responses.get(URL, json={"ok": True})
    assert make_client().request("GET", "/thing") == {"ok": True}
    assert responses.calls[0].request.headers["Authorization"] == "Bearer t1"


@responses.activate
def test_an_empty_answer_is_an_empty_dict():
    responses.post(URL, status=201)
    assert make_client().request("POST", "/thing", json={}) == {}


@responses.activate
def test_refreshes_the_token_once_on_401():
    responses.get(URL, status=401)
    responses.get(URL, json={"ok": True})
    assert make_client().request("GET", "/thing") == {"ok": True}
    assert responses.calls[1].request.headers["Authorization"] == "Bearer t2"


@responses.activate
def test_a_second_401_is_an_error_not_a_loop():
    responses.get(URL, status=401, json={"error": {"status": 401, "message": "expired"}})
    with pytest.raises(ApiError) as exc:
        make_client().request("GET", "/thing")
    assert exc.value.status == 401 and len(responses.calls) == 2


@responses.activate
def test_429_waits_for_retry_after_and_retries():
    responses.get(URL, status=429, headers={"Retry-After": "3"})
    responses.get(URL, json={"ok": True})
    sleeps: list[float] = []
    assert make_client(sleeps).request("GET", "/thing") == {"ok": True}
    assert sleeps == [3.0]


@responses.activate
@pytest.mark.parametrize(
    ("asked", "waited"),
    [("600", 60.0), ("inf", 60.0), ("-5", 1.0), ("nan", 1.0), ("Wed, 21 Oct 2026 07:28:00 GMT", 1.0)],
)
def test_a_retry_after_that_is_no_sensible_wait_does_not_break_the_retry(asked, waited):
    # time.sleep() raises on a negative number and on NaN; an hour would look like a hang.
    responses.get(URL, status=429, headers={"Retry-After": asked})
    responses.get(URL, json={"ok": True})
    sleeps: list[float] = []
    assert make_client(sleeps).request("GET", "/thing") == {"ok": True}
    assert sleeps == [waited]


@responses.activate
def test_quota_exceeded_fails_at_once_without_waiting():
    responses.get(URL, status=429, json={"error": {"status": 429, "reason": "QUOTA_EXCEEDED"}})
    sleeps: list[float] = []
    with pytest.raises(QuotaExceeded):
        make_client(sleeps).request("GET", "/thing")
    assert sleeps == [] and len(responses.calls) == 1


@responses.activate
def test_gives_up_after_max_retries():
    responses.get(URL, status=429)
    with pytest.raises(ApiError) as exc:
        make_client(max_retries=2).request("GET", "/thing")
    assert exc.value.status == 429 and len(responses.calls) == 3


@responses.activate
def test_server_errors_are_retried_with_backoff():
    responses.get(URL, status=503)
    responses.get(URL, status=503)
    responses.get(URL, json={"ok": True})
    sleeps: list[float] = []
    assert make_client(sleeps).request("GET", "/thing") == {"ok": True}
    assert sleeps == [1.0, 2.0]


@responses.activate
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"error": {"status": 403, "message": "Forbidden by Spotify"}}, "Forbidden by Spotify"),
        ({"errors": [{"title": "Forbidden", "detail": "Missing scope"}]}, "Missing scope"),
        ({"error": "invalid_grant", "error_description": "Bad code"}, "Bad code"),
    ],
)
def test_error_messages_from_all_three_api_styles(body, expected):
    responses.get(URL, status=403, json=body)
    with pytest.raises(ApiError, match=expected) as exc:
        make_client().request("GET", "/thing")
    assert exc.value.status == 403


@responses.activate
def test_a_network_failure_is_a_network_error():
    responses.get(URL, body=requests.ConnectionError("no route to host"))
    with pytest.raises(NetworkError, match=r"api\.test"):
        make_client().request("GET", "/thing")


@responses.activate
def test_full_urls_of_the_api_are_used_as_they_are():
    responses.get("https://api.test/page2?offset=50", json={"page": 2})
    assert make_client().request("GET", "https://api.test/page2?offset=50") == {"page": 2}


@responses.activate
@pytest.mark.parametrize(
    "link",
    [
        "https://elsewhere.test/page2",
        "http://api.test/page2",  # our host, but unencrypted
        "https://api.test.evil.example/page2",
        "https://api.test@evil.example/page2",
        "https://api.test:8443/page2",
    ],
)
def test_the_token_is_never_sent_to_another_address(link):
    # A "next page" link comes out of a response; the token must not follow it off the API.
    with pytest.raises(NetworkError, match="another address"):
        make_client().request("GET", link)
    assert len(responses.calls) == 0
