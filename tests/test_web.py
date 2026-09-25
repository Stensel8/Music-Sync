import io
import re
import time

import pytest
from flask.testing import FlaskClient

from musicsync.config import Settings, config_path
from musicsync.csvio import parse_csv
from musicsync.errors import ApiError
from musicsync.models import Track
from musicsync.oauth import Token
from musicsync.sync import Miss, Step
from musicsync.web import create_app
from musicsync.web.jobs import Job

from .support import ISRC_A, FakeProvider, FakeServices, track

CATALOG = [
    track("Song A", "Artist A", ids={"tidal": "1"}, isrc=ISRC_A),
    track("Song B", "Artist B", ids={"tidal": "2"}),
]
JSON = {"Accept": "application/json"}  # what the page's own fetch() calls send


@pytest.fixture
def services(tmp_path) -> FakeServices:
    spotify = FakeProvider("spotify", liked=[Track("Song A", ["Artist A"], isrc=ISRC_A), Track("Nope", ["Nobody"])])
    return FakeServices(tmp_path, spotify=spotify, tidal=FakeProvider("tidal", CATALOG))


@pytest.fixture
def client(services) -> FlaskClient:
    return create_app(services, secret_key="test-secret").test_client()


def wait_for(client: FlaskClient, job_id: str, timeout: float = 5) -> dict:
    """Poll a background job until it is no longer running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}", headers=JSON).get_json()
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def csv_tracks(response) -> list[str]:
    return [t.title for t in parse_csv(io.StringIO(response.get_data(as_text=True)))]


# --- pages and hardening ----------------------------------------------------------------------------


def test_the_pages_render(client):
    assert "Dashboard" in client.get("/").get_data(as_text=True)
    login = client.get("/login").get_data(as_text=True)
    assert "with Spotify" in login and "with Tidal" in login


def test_the_pages_load_nothing_from_other_sites(client, services):
    services.store.save("spotify", Token("A", "R", time.time() + 100))
    for path in ("/", "/login", "/spotify/account"):
        page = client.get(path).get_data(as_text=True)
        assert "/static/style.css" in page and "/static/app.js" in page, path
        assert not re.search(r"(?:src|href)=[\"']?https?:", page), path
    assert client.get("/static/style.css").mimetype == "text/css"
    assert "javascript" in client.get("/static/app.js").mimetype


@pytest.fixture
def unconfigured(tmp_path) -> FlaskClient:
    """No client IDs at all, but an old Spotify login still in the token file."""
    services = FakeServices(tmp_path, Settings())
    services.store.save("spotify", Token("A", "R", time.time() + 100))
    return create_app(services, secret_key="k").test_client()


def test_a_service_without_a_client_id_leads_to_the_setup_page(unconfigured):
    page = unconfigured.get("/login").get_data(as_text=True)
    assert "Set up Spotify" in page and "/spotify/setup" in page and "with Spotify" not in page
    for path in ("/spotify/login", "/spotify/account", "/tidal/account"):
        response = unconfigured.get(path)
        assert response.status_code == 302 and str(response.location).endswith(path.rsplit("/", 1)[0] + "/setup")


def test_an_old_login_without_a_client_id_does_not_count_as_connected(unconfigured):
    page = unconfigured.get("/").get_data(as_text=True)
    assert "Connected" not in page and "Client ID missing" in page and "/spotify/setup" in page
    assert '<option value="spotify">' not in page  # it cannot be picked for an export or transfer


@pytest.mark.parametrize(
    ("service", "dashboard", "secret"),
    [
        ("spotify", "https://developer.spotify.com/dashboard", False),
        ("tidal", "https://developer.tidal.com/dashboard", True),
    ],
)
def test_the_setup_page_says_where_to_get_the_client_id_and_where_to_put_it(unconfigured, service, dashboard, secret):
    page = unconfigured.get(f"/{service}/setup").get_data(as_text=True)
    assert f'href="{dashboard}"' in page and 'rel="noopener noreferrer"' in page
    assert f"http://127.0.0.1:8888/{service}/callback" in page and str(config_path()) in page
    assert f"[{service}]" in page and ("client_secret =" in page) is secret


def test_the_done_button_logs_in_once_the_client_id_is_there(tmp_path, unconfigured):
    assert "no Spotify client ID in the settings file yet" in unconfigured.get("/spotify/setup?check=1").get_data(
        as_text=True
    )
    configured = create_app(FakeServices(tmp_path), secret_key="k").test_client()
    response = configured.get("/spotify/setup?check=1")
    assert response.status_code == 302 and str(response.location).endswith("/spotify/login")
    assert "has a client ID" in configured.get("/spotify/setup").get_data(as_text=True)


def test_only_our_own_host_names_are_answered(client):
    assert client.get("/", headers={"Host": "localhost:8888"}).status_code == 200
    assert client.get("/", headers={"Host": "127.0.0.1:8888"}).status_code == 200
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_responses_carry_security_headers(client):
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff" and headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 302),  # no browser headers: curl or a script
        ({"Sec-Fetch-Site": "same-origin"}, 302),
        ({"Sec-Fetch-Site": "none"}, 302),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Sec-Fetch-Site": "same-site"}, 403),  # another port on this machine is another origin too
        ({"Origin": "http://localhost"}, 302),  # the test client's own address
        ({"Origin": "https://evil.example"}, 403),
        ({"Origin": "null"}, 403),
    ],
)
def test_changes_started_by_another_site_are_refused(client, headers, status):
    assert client.post("/spotify/logout", headers=headers).status_code == status


def test_refusals_are_json_for_fetch_calls_and_a_page_for_browsers(client):
    forged = {"Sec-Fetch-Site": "cross-site"}
    assert client.post("/transfer", headers={**forged, **JSON}).get_json()["status"] == "error"
    assert "Error 403" in client.post("/transfer", headers=forged).get_data(as_text=True)


# --- logging in ----------------------------------------------------------------------------------------


def test_login_redirects_to_the_service_and_remembers_the_state(client):
    response = client.get("/spotify/login")
    assert response.status_code == 302 and str(response.location).startswith("https://auth.test/authorize?state=")
    with client.session_transaction() as session:
        assert session["spotify_oauth"]["state"] in str(response.location) and session["spotify_oauth"]["verifier"]


def test_the_callback_stores_the_token_and_forgets_the_verifier(client, services):
    client.get("/spotify/login")
    with client.session_transaction() as session:
        state, verifier = session["spotify_oauth"]["state"], session["spotify_oauth"]["verifier"]
    response = client.get(f"/spotify/callback?code=CODE&state={state}")
    assert response.status_code == 302 and str(response.location).endswith("/spotify/account")
    assert services.fake_oauth.exchanged == ("CODE", verifier)
    token = services.store.load("spotify")
    assert token is not None and token.access_token == "ACCESS"
    with client.session_transaction() as session:
        assert "spotify_oauth" not in session


def test_the_callback_rejects_a_wrong_state_and_a_replay(client, services):
    client.get("/spotify/login")
    assert client.get("/spotify/callback?code=CODE&state=wrong").status_code == 400
    assert services.store.load("spotify") is None
    assert client.get("/spotify/callback?code=CODE&state=wrong").status_code == 400  # the login is used up


def test_the_callback_reports_a_refusal(client):
    client.get("/tidal/login")
    response = client.get("/tidal/callback?error=access_denied")
    assert response.status_code == 400 and "access_denied" in response.get_data(as_text=True)


def test_logging_out_removes_the_token_and_needs_a_post(client, services):
    services.store.save("spotify", Token("A", "R", time.time() + 100))
    assert client.get("/spotify/logout").status_code == 405  # a link on some other page cannot log you out
    assert client.post("/spotify/logout").status_code == 302
    assert services.store.load("spotify") is None


# --- account, export ---------------------------------------------------------------------------------


def test_the_account_page_lists_playlists_and_escapes_their_names(client, services):
    services.providers["spotify"].existing_playlist("<b>Bold</b> mix", track("T"))  # type: ignore[attr-defined]
    page = client.get("/spotify/account").get_data(as_text=True)
    assert "&lt;b&gt;Bold&lt;/b&gt; mix" in page and "<b>Bold</b>" not in page
    assert 'data-service="spotify" data-export="pl1"' in page


def test_playlists_you_cannot_read_have_no_export_link(client, services):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)
    spotify.existing_playlist("Followed")
    spotify.readable = False
    page = client.get("/spotify/account").get_data(as_text=True)
    assert "cannot be read" in page and 'data-export="pl1"' not in page


def test_empty_playlists_have_no_export_link_and_are_refused(client, services):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)
    spotify.existing_playlist("Empty")
    page = client.get("/spotify/account").get_data(as_text=True)
    assert "is empty" in page and 'data-export="pl1"' not in page
    job = wait_for(client, export(client, "spotify", "Empty").get_json()["id"])
    assert job["status"] == "error" and "is empty" in job["message"]


def test_the_account_page_sends_you_to_login_when_the_session_is_gone(tmp_path):
    from musicsync.errors import NotLoggedIn

    class LoggedOut(FakeServices):
        def provider(self, service):
            raise NotLoggedIn("Not logged in")

    response = create_app(LoggedOut(tmp_path), secret_key="k").test_client().get("/tidal/account")
    assert response.status_code == 302 and str(response.location).endswith("/tidal/login")


def transfer(client: FlaskClient, **extra):
    return client.post("/transfer", json={"source": "spotify", "target": "tidal", **extra}, headers=JSON)


def export(client: FlaskClient, service: str, playlist: str | None = None):
    return client.post(f"/{service}/export", json={"playlist": playlist}, headers=JSON)


def test_export_runs_as_a_job_and_then_downloads_the_csv(client, services):
    response = export(client, "spotify")
    assert response.status_code == 202
    job_id = response.get_json()["id"]
    job = wait_for(client, job_id)
    assert job["status"] == "done" and job["message"] == "Liked Songs: 2 tracks exported."
    assert job["download"] and job["phases"] == ["read"] and job["title"] == "Export from Spotify"
    download = client.get(f"/jobs/{job_id}/download")
    assert download.mimetype == "text/csv" and "attachment" in download.headers["Content-Disposition"]
    assert csv_tracks(download) == ["Song A", "Nope"]

    tidal = services.providers["tidal"]
    assert isinstance(tidal, FakeProvider)
    tidal.existing_playlist("R\u00f6ad trip", track("In list"))
    job_id = export(client, "tidal", "R\u00f6ad trip").get_json()["id"]
    assert wait_for(client, job_id)["status"] == "done"
    disposition = client.get(f"/jobs/{job_id}/download").headers["Content-Disposition"]
    # The name with its accent for browsers that read filename*, an ASCII one for the rest.
    assert "filename*=UTF-8''R%C3%B6ad%20trip.csv" in disposition and 'filename="Rad trip.csv"' in disposition

    job = wait_for(client, export(client, "tidal", "missing").get_json()["id"])
    assert job["status"] == "error" and "No playlist" in job["message"]


def test_only_an_export_has_a_download(client):
    job_id = transfer(client).get_json()["id"]
    wait_for(client, job_id)
    assert client.get(f"/jobs/{job_id}/download").status_code == 404


# --- jobs: import and transfer ------------------------------------------------------------------------


def upload(client: FlaskClient, text: bytes, playlist: str = "Mix", name: str = "in.csv"):
    return client.post(
        "/tidal/import",
        data={"file": (io.BytesIO(text), name), "playlist": playlist},
        headers=JSON,
        content_type="multipart/form-data",
    )


def test_import_runs_as_a_job_and_reports_what_was_not_found(client, services):
    response = upload(client, b"Artist A,Song A\nNobody,Nothing\n")
    assert response.status_code == 202
    job_id = response.get_json()["id"]
    job = wait_for(client, job_id)
    assert job["status"] == "done" and job["message"] == "Mix: 1 matched, 1 not found; added 1"
    assert job["unmatched"] == [{"track": "Nobody - Nothing", "why": "not on Tidal"}]
    assert (job["found"], job["not_found"], job["phase"], job["done"], job["total"]) == (1, 1, "add", 1, 1)
    assert job["phases"] == ["check", "match", "add"] and job["eta"] is None

    tidal = services.providers["tidal"]
    assert isinstance(tidal, FakeProvider)
    assert [tidal.native_id(t) for t in tidal.playlists_by_id["pl1"][1]] == ["1"]
    assert csv_tracks(client.get(f"/jobs/{job_id}/unmatched.csv")) == ["Nothing"]


def test_import_can_add_albums(client, services):
    tidal = services.providers["tidal"]
    assert isinstance(tidal, FakeProvider)
    tidal.albums = [track("Discovery", "Daft Punk", ids={"tidal": "5"})]
    response = client.post(
        "/tidal/import",
        data={"file": (io.BytesIO(b"Daft Punk,Discovery\n"), "albums.csv"), "contains": "albums"},
        headers=JSON,
        content_type="multipart/form-data",
    )
    job = wait_for(client, response.get_json()["id"])
    assert job["message"] == "Favourite albums: 1 matched, 0 not found; added 1"
    assert [a.ids for a in tidal.favorite_albums] == [{"tidal": "5"}]


@pytest.mark.parametrize(
    ("body", "message"),
    [(b"", "no tracks"), ("Björk,Jóga".encode("latin-1"), "UTF-8")],
)
def test_import_rejects_empty_and_non_utf8_files(client, body, message):
    response = upload(client, body)
    assert response.status_code == 400 and message in response.get_json()["message"]


def test_import_needs_a_file(client):
    assert client.post("/tidal/import", data={}, headers=JSON).status_code == 400


def test_transfer_can_go_to_the_favourites(client, services):
    job = wait_for(client, transfer(client, into="favorites").get_json()["id"])
    tidal = services.providers["tidal"]
    assert isinstance(tidal, FakeProvider)
    assert job["status"] == "done" and job["message"].startswith("Liked Songs: 1 matched")
    assert tidal.playlists_by_id == {} and [tidal.native_id(t) for t in tidal.liked] == ["1"]


def test_transfer_copies_liked_songs_to_the_other_service(client, services):
    response = transfer(client)
    job = wait_for(client, response.get_json()["id"])
    assert job["status"] == "done" and "Liked Songs (from Spotify): 1 matched, 1 not found" in job["message"]
    assert job["title"] == "Transfer from Spotify to Tidal" and job["phases"] == ["read", "check", "match", "add"]
    tidal = services.providers["tidal"]
    assert isinstance(tidal, FakeProvider)
    assert [name for name, _ in tidal.playlists_by_id.values()] == ["Liked Songs (from Spotify)"]


@pytest.mark.parametrize("body", [{"source": "tidal", "target": "tidal"}, {"source": "x", "target": "tidal"}, {}])
def test_transfer_validates_its_input(client, body):
    response = client.post("/transfer", json=body, headers=JSON)
    assert response.status_code == 400 and response.get_json()["status"] == "error"


def test_a_failing_job_reports_the_reason(tmp_path):
    class Broken(FakeServices):
        def provider(self, service):
            raise ApiError(403, "Forbidden by the service")

    client = create_app(Broken(tmp_path), secret_key="k").test_client()
    # The providers are built while the request is handled, so this fails the request itself.
    response = transfer(client)
    assert response.status_code == 502 and "Forbidden by the service" in response.get_json()["message"]


def test_an_error_inside_a_job_is_reported_through_the_job(services, client):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)

    def explode():
        raise ApiError(500, "The service fell over")

    spotify.liked_tracks = explode  # type: ignore[method-assign]
    job = wait_for(client, transfer(client).get_json()["id"])
    assert job["status"] == "error" and "fell over" in job["message"]


def test_a_bug_inside_a_job_does_not_leak_details(services, client):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)

    def crash():
        raise RuntimeError("secret internal detail")

    spotify.liked_tracks = crash  # type: ignore[method-assign]
    job = wait_for(client, transfer(client).get_json()["id"])
    assert job["status"] == "error" and "secret internal detail" not in job["message"]


def test_a_track_not_found_comes_with_what_came_closest(client, services):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)
    spotify.liked = [Track("Song B (Live)", ["Artist B"])]  # Tidal only has the studio version
    job = wait_for(client, transfer(client).get_json()["id"])
    assert job["unmatched"] == [
        {"track": "Artist B - Song B (Live)", "why": "only another version; closest: Artist B - Song B"}
    ]


def test_a_running_job_says_what_it_is_doing():
    """The page shows the step, how far it is, and what it could not find so far."""
    job = Job("id", "transfer", "Transfer from Spotify to Tidal")
    job.progress(Step("read", "Reading Liked Songs from Spotify", 10, None))
    assert (job.phase, job.done, job.total) == ("read", 10, None)
    for done, title in enumerate("AB", start=1):
        track = Track(title, ["X"])
        job.progress(Step("match", "Finding the tracks on Tidal", done, 3, track, 0, Miss(track, "not on Tidal")))
    shown = job.to_json()
    assert (shown["phase"], shown["done"], shown["total"]) == ("match", 2, 3)
    assert shown["text"] == "Finding the tracks on Tidal"
    assert (shown["found"], shown["not_found"], shown["current"]) == (0, 2, "X - B")
    assert shown["status"] == "running" and not shown["download"]


def test_unknown_jobs_are_a_404(client):
    assert client.get("/jobs/nope", headers=JSON).status_code == 404
    assert client.get("/jobs/nope/unmatched.csv").status_code == 404
    assert client.get("/jobs/nope/download").status_code == 404
