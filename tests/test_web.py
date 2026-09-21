import io
import time

import pytest
from flask.testing import FlaskClient

from musicsync.csvio import parse_csv
from musicsync.errors import ApiError
from musicsync.models import Track
from musicsync.oauth import Token
from musicsync.web import create_app

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
    assert "met Spotify" in login and "met Tidal" in login


def test_a_service_without_a_client_id_says_so(tmp_path):
    from musicsync.config import Settings

    page = (
        create_app(FakeServices(tmp_path, Settings()), secret_key="k")
        .test_client()
        .get("/login")
        .get_data(as_text=True)
    )
    assert "client-ID ontbreekt" in page and "met Spotify" not in page


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
    assert "Fout 403" in client.post("/transfer", headers=forged).get_data(as_text=True)


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
    assert "/spotify/export?playlist=pl1" in page


def test_playlists_you_cannot_read_have_no_export_link(client, services):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)
    spotify.existing_playlist("Followed")
    spotify.readable = False
    page = client.get("/spotify/account").get_data(as_text=True)
    assert "niet leesbaar" in page and "playlist=pl1" not in page


def test_the_account_page_sends_you_to_login_when_the_session_is_gone(tmp_path):
    from musicsync.errors import NotLoggedIn

    class LoggedOut(FakeServices):
        def provider(self, service):
            raise NotLoggedIn("Not logged in")

    response = create_app(LoggedOut(tmp_path), secret_key="k").test_client().get("/tidal/account")
    assert response.status_code == 302 and str(response.location).endswith("/tidal/login")


def test_export_liked_songs_and_a_playlist_as_csv(client, services):
    response = client.get("/spotify/export")
    assert response.mimetype == "text/csv" and "attachment" in response.headers["Content-Disposition"]
    assert csv_tracks(response) == ["Song A", "Nope"]

    tidal = services.providers["tidal"]
    assert isinstance(tidal, FakeProvider)
    tidal.existing_playlist("Road trip", track("In list"))
    response = client.get("/tidal/export?playlist=Road trip")
    assert "Road%20trip.csv" in response.headers["Content-Disposition"]
    assert csv_tracks(response) == ["In list"]
    assert client.get("/tidal/export?playlist=missing", headers=JSON).status_code == 400


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
    assert job["status"] == "done" and job["message"] == "Mix: 1 gevonden, 1 niet gevonden; 1 toegevoegd"
    assert job["unmatched"] == ["Nobody - Nothing"] and job["done"] == job["total"] == 2

    tidal = services.providers["tidal"]
    assert isinstance(tidal, FakeProvider)
    assert [tidal.native_id(t) for t in tidal.playlists_by_id["pl1"][1]] == ["1"]
    assert csv_tracks(client.get(f"/jobs/{job_id}/unmatched.csv")) == ["Nothing"]


@pytest.mark.parametrize(
    ("body", "message"),
    [(b"", "geen nummers"), ("Björk,Jóga".encode("latin-1"), "UTF-8")],
)
def test_import_rejects_empty_and_non_utf8_files(client, body, message):
    response = upload(client, body)
    assert response.status_code == 400 and message in response.get_json()["message"]


def test_import_needs_a_file(client):
    assert client.post("/tidal/import", data={}, headers=JSON).status_code == 400


def test_transfer_copies_liked_songs_to_the_other_service(client, services):
    response = client.post("/transfer", json={"source": "spotify", "target": "tidal"}, headers=JSON)
    job = wait_for(client, response.get_json()["id"])
    assert job["status"] == "done" and "Liked Songs (from Spotify): 1 gevonden, 1 niet gevonden" in job["message"]
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
    response = client.post("/transfer", json={"source": "spotify", "target": "tidal"}, headers=JSON)
    assert response.status_code == 502 and "Forbidden by the service" in response.get_json()["message"]


def test_an_error_inside_a_job_is_reported_through_the_job(services, client):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)

    def explode():
        raise ApiError(500, "The service fell over")

    spotify.liked_tracks = explode  # type: ignore[method-assign]
    job = wait_for(
        client, client.post("/transfer", json={"source": "spotify", "target": "tidal"}, headers=JSON).get_json()["id"]
    )
    assert job["status"] == "error" and "fell over" in job["message"]


def test_a_bug_inside_a_job_does_not_leak_details(services, client):
    spotify = services.providers["spotify"]
    assert isinstance(spotify, FakeProvider)

    def crash():
        raise RuntimeError("secret internal detail")

    spotify.liked_tracks = crash  # type: ignore[method-assign]
    job = wait_for(
        client, client.post("/transfer", json={"source": "spotify", "target": "tidal"}, headers=JSON).get_json()["id"]
    )
    assert job["status"] == "error" and "secret internal detail" not in job["message"]


def test_unknown_jobs_are_a_404(client):
    assert client.get("/jobs/nope", headers=JSON).status_code == 404
    assert client.get("/jobs/nope/unmatched.csv").status_code == 404
