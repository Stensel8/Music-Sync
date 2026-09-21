import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Never let a test read the developer's real config, tokens or API credentials."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    for service in ("SPOTIFY", "TIDAL"):
        for setting in ("CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
            monkeypatch.delenv(f"{service}_{setting}", raising=False)
    for name in ("MUSICSYNC_COUNTRY", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
