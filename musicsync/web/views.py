"""The pages and actions of the web interface."""

import io
import secrets
from collections.abc import Callable
from urllib.parse import quote

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request, session, url_for
from werkzeug.wrappers import Response as WerkzeugResponse

from ..config import SERVICES, config_path
from ..csvio import parse_csv, slug, write_csv
from ..errors import CsvError, NotLoggedIn, ProviderError
from ..oauth import pkce_pair
from ..services import Services
from ..sync import LIKED, import_tracks, select_tracks
from .jobs import Job, JobManager

pages = Blueprint("pages", __name__)
# Everything that belongs to one service lives under /spotify/... or /tidal/...
service_pages = Blueprint("service", __name__, url_prefix=f"/<any({','.join(SERVICES)}):service>")


def _services() -> Services:
    return current_app.extensions["services"]


def _jobs() -> JobManager:
    return current_app.extensions["jobs"]


def _accounts() -> list[dict]:
    """What the templates need to know about each service."""
    services = _services()
    return [
        {
            "name": name,
            "label": name.title(),
            "configured": services.settings.is_configured(name),
            "connected": services.store.load(name) is not None,
        }
        for name in SERVICES
    ]


def _start(kind: str, title: str, work: Callable[[Job], str]) -> tuple[Response, int]:
    """Run ``work(job)`` in the background. The page follows its progress at /jobs/<id>."""
    return jsonify(id=_jobs().start(kind, title, work).id), 202


def _job(job_id: str) -> Job:
    if (job := _jobs().get(job_id)) is None:
        abort(404, "Unknown job")
    return job


def _attachment(text: str, name: str) -> Response:
    """A CSV the browser saves as ``name``.csv."""
    # filename* carries the real name (it may have accents); plain filename is the ASCII fallback for old
    # browsers, as a header cannot carry every character.
    fallback = slug(name).encode("ascii", "ignore").decode().strip() or "export"
    disposition = f"attachment; filename=\"{fallback}.csv\"; filename*=UTF-8''{quote(slug(name))}.csv"
    return Response(text, mimetype="text/csv", headers={"Content-Disposition": disposition})


# --- pages and cross-service actions ---------------------------------------------------------------


@pages.get("/")
def dashboard() -> str:
    return render_template("dashboard.html", accounts=_accounts())


@pages.get("/login")
def login_page() -> str:
    return render_template("login.html", accounts=_accounts(), config_file=config_path())


@pages.post("/transfer")
def transfer() -> tuple[Response, int]:
    data = request.get_json(silent=True) or {}
    source, target = data.get("source"), data.get("target")
    if source not in SERVICES or target not in SERVICES or source == target:
        raise ProviderError("Choose two different services.")
    playlist = data.get("playlist") or None
    name = (data.get("name") or "").strip()
    source_provider, target_provider = _services().provider(source), _services().provider(target)

    def work(job: Job) -> str:
        ((source_name, tracks),) = select_tracks(
            source_provider, liked=not playlist, playlist=playlist, progress=job.progress
        )
        destination = name or (f"{LIKED} (from {source.title()})" if source_name == LIKED else source_name)
        return import_tracks(target_provider, tracks, destination, progress=job.progress).summary()

    return _start("transfer", f"Transfer from {source.title()} to {target.title()}", work)


@pages.get("/jobs/<job_id>")
def job_status(job_id: str) -> Response:
    return jsonify(_job(job_id).to_json())


@pages.get("/jobs/<job_id>/unmatched.csv")
def job_unmatched(job_id: str) -> Response:
    out = io.StringIO()
    write_csv(out, _job(job_id).unmatched)
    return _attachment(out.getvalue(), "unmatched")


@pages.get("/jobs/<job_id>/download")
def job_download(job_id: str) -> Response:
    """The CSV an export made."""
    if (download := _job(job_id).download) is None:
        abort(404, "This job has no file (yet).")
    name, text = download
    return _attachment(text, name)


# --- one service: login, account, export, import --------------------------------------------------


@service_pages.get("/login")
def login(service: str) -> WerkzeugResponse:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    session[f"{service}_oauth"] = {"verifier": verifier, "state": state}  # needed again on the way back
    return redirect(_services().oauth(service).authorize_url(state, challenge))


@service_pages.get("/callback")
def callback(service: str) -> WerkzeugResponse:
    saved = session.pop(f"{service}_oauth", None)  # popped: a login can only be completed once
    if error := request.args.get("error"):
        raise ProviderError(f"Login to {service.title()} was refused: {error}")
    if not saved or request.args.get("state") != saved["state"]:
        raise ProviderError("State mismatch (possible CSRF attack). Please log in again.")
    if not (code := request.args.get("code")):
        raise ProviderError("No authorization code received.")
    services = _services()
    services.store.save(service, services.oauth(service).exchange(code, saved["verifier"]))
    return redirect(url_for("service.account", service=service))


@service_pages.get("/account")
def account(service: str) -> str | WerkzeugResponse:
    try:
        playlists = _services().provider(service).playlists()
    except NotLoggedIn:
        return redirect(url_for("service.login", service=service))
    return render_template("account.html", service=service, label=service.title(), playlists=playlists)


@service_pages.post("/logout")  # a POST, so that a link on some other page cannot log you out
def logout(service: str) -> WerkzeugResponse:
    _services().store.delete(service)
    return redirect(url_for("pages.login_page"))


@service_pages.get("/playlists")
def playlists(service: str) -> Response:
    return jsonify(
        [
            {"id": p.id, "name": p.name, "track_count": p.track_count, "readable": p.readable}
            for p in _services().provider(service).playlists()
        ]
    )


@service_pages.post("/export")
def export(service: str) -> tuple[Response, int]:
    """Read liked songs or a playlist in the background; the page downloads the CSV at /jobs/<id>/download."""
    playlist = (request.get_json(silent=True) or {}).get("playlist") or None
    provider = _services().provider(service)  # built here, so missing credentials fail the request, not the job

    def work(job: Job) -> str:
        ((name, tracks),) = select_tracks(provider, liked=not playlist, playlist=playlist, progress=job.progress)
        out = io.StringIO()
        write_csv(out, tracks)
        job.download = (name, out.getvalue())
        return f"{name}: {len(tracks)} tracks exported."

    return _start("export", f"Export from {service.title()}", work)


@service_pages.post("/import")
def import_csv(service: str) -> tuple[Response, int]:
    upload = request.files.get("file")
    if not upload or not upload.filename:
        raise CsvError("Choose a CSV file first.")
    try:
        text = upload.stream.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvError("The file is not UTF-8.") from exc
    if not (tracks := parse_csv(io.StringIO(text, newline=""), upload.filename)):
        raise CsvError("There are no tracks in this file.")
    playlist = request.form.get("playlist", "").strip() or "Music-Sync import"
    provider = _services().provider(service)  # built here, so missing credentials fail the request, not the job

    def work(job: Job) -> str:
        return import_tracks(provider, tracks, playlist, progress=job.progress).summary()

    return _start("import", f"Import into {service.title()}", work)
