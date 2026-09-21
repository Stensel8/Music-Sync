"""The local web interface: the same export, import and transfer as the CLI, in a browser."""

import os
import secrets
from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, SecurityError

from ..config import config_dir, write_private
from ..errors import MusicSyncError
from ..services import Services
from .jobs import JobManager
from .views import pages, service_pages


def _secret_key() -> str:
    """The key that signs the session cookie: $SECRET_KEY, else one made once and kept next to the tokens."""
    if key := os.environ.get("SECRET_KEY"):
        return key
    path = config_dir() / "web-secret-key"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        key = secrets.token_hex(32)
        write_private(path, key)
        return key


def _reject_cross_origin() -> None:
    """Refuse changes started by another website (CSRF). Browsers say where a request came from
    (``Sec-Fetch-Site``, ``Origin``) and page scripts cannot forge that, so no tokens are needed."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    site = request.headers.get("Sec-Fetch-Site")
    if site is not None:
        allowed = site in ("same-origin", "none")  # "none": the user typed the address or used a bookmark
    else:
        # Browsers from before 2023 only send Origin, so compare that with our own address. A
        # request with neither header does not come from a web page (curl, the tests), so it is fine.
        origin = request.headers.get("Origin")
        allowed = origin is None or urlsplit(origin).netloc == request.host
    if not allowed:
        abort(403, "Request from another website refused.")


def _security_headers(response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _report(exc: Exception) -> tuple[Response | str, int]:
    """Answer a MusicSyncError or an HTTP error: JSON for the page's own fetch() calls, else an error page."""
    if isinstance(exc, MusicSyncError):
        status, message = exc.http_status, str(exc)
    elif isinstance(exc, HTTPException):
        status, message = exc.code or 500, exc.description or ""
    else:
        raise exc
    if request.accept_mimetypes.best == "application/json":
        return jsonify(status="error", message=message), status
    return render_template("error.html", message=message, status=status), status


def _unknown_host(_exc: SecurityError) -> Response:
    """A Host header that is not ours. Flask cannot build URLs for it, so no template."""
    return Response("Unknown host name.", status=400, mimetype="text/plain")


def create_app(services: Services | None = None, secret_key: str | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=secret_key or _secret_key(),
        # Only answer to our own address, so a page on another site cannot reach us under its
        # own name (DNS rebinding).
        TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"],
        SESSION_COOKIE_SAMESITE="Lax",  # Lax still sends the cookie on the redirect back from Spotify or Tidal
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,  # a CSV with thousands of tracks is far smaller than this
    )
    app.extensions["services"] = services or Services()
    app.extensions["jobs"] = JobManager()

    app.before_request(_reject_cross_origin)
    app.after_request(_security_headers)
    app.register_error_handler(SecurityError, _unknown_host)
    app.register_error_handler(MusicSyncError, _report)
    app.register_error_handler(HTTPException, _report)
    app.register_blueprint(pages)
    app.register_blueprint(service_pages)
    return app
