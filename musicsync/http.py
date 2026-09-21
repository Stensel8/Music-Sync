"""A small JSON API client: bearer auth, token refresh on 401, retry on 429 and 5xx."""

import logging
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import requests

from .errors import ApiError, NetworkError, QuotaExceeded

log = logging.getLogger(__name__)

# Returns the current access token. It is called with True when the server rejected the last one.
type TokenProvider = Callable[[bool], str]
type Json = dict[str, Any]


def json_body(resp: requests.Response) -> Json:
    """The body as a dict; {} when there is none or it is not JSON (a 201 or 204 answer, say)."""
    try:
        body = resp.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def error_message(resp: requests.Response) -> str:
    """The error text of a failed response, whichever of the two API styles sent it."""
    body = json_body(resp)
    error = body.get("error")
    if isinstance(error, dict) and error.get("message"):  # Spotify: {"error": {"message": ...}}
        return str(error["message"])
    if isinstance(error, str):  # OAuth token endpoints: {"error": "...", "error_description": "..."}
        return str(body.get("error_description") or error)
    errors = body.get("errors")  # JSON:API (Tidal): {"errors": [{"detail": ...}]}
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("detail") or errors[0].get("title") or errors[0])
    return resp.text[:200] or resp.reason or "request failed"


def _quota_exceeded(resp: requests.Response) -> bool:
    """Spotify's "your app used up its quota" 429, which no amount of waiting will fix."""
    error = json_body(resp).get("error")
    return resp.status_code == 429 and isinstance(error, dict) and error.get("reason") == "QUOTA_EXCEEDED"


def _retry_delay(resp: requests.Response, attempt: int) -> float:
    """Seconds to wait: what the server asks for (at most a minute), else exponential backoff."""
    try:
        return min(float(resp.headers["Retry-After"]), 60.0)
    except KeyError, ValueError:
        return min(2.0**attempt, 60.0)


class ApiClient:
    def __init__(
        self,
        base_url: str,
        token: TokenProvider,
        *,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
        timeout: float = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.headers = headers or {}
        self.session = session or requests.Session()  # keeps connections open between requests
        self.sleep = sleep  # injectable so tests do not really wait
        self.max_retries = max_retries
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Json | None = None,
        headers: dict[str, str] | None = None,
    ) -> Json:
        """Send a request and return the JSON answer. ``path`` may be a full URL (a "next page" link)."""
        url = path if path.startswith("http") else self.base_url + path
        attempt, refreshed, force_refresh = 0, False, False
        while True:
            auth = {"Authorization": f"Bearer {self.token(force_refresh)}"}
            force_refresh = False
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={**self.headers, **(headers or {}), **auth},
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise NetworkError(f"could not reach {urlsplit(url).netloc}: {exc}") from exc
            log.debug("%s %s -> %s", method, url, resp.status_code)

            if resp.status_code == 401 and not refreshed:
                refreshed = force_refresh = True  # the token may just have expired: refresh it, once
                continue
            if _quota_exceeded(resp):
                raise QuotaExceeded(429, "the app's API quota is used up; try again later")
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.max_retries:
                self.sleep(_retry_delay(resp, attempt))
                attempt += 1
                continue
            if resp.ok:
                return json_body(resp)
            raise ApiError(resp.status_code, error_message(resp))
