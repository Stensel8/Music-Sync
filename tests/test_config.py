import pytest

from musicsync.config import ServiceConfig, Settings, load_settings
from musicsync.errors import ConfigError


def test_environment_variables_override_the_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'country = "nl"\n[spotify]\nclient_id = "from-file"\n[tidal]\nclient_id = "t"\nclient_secret = "s"\n'
    )
    settings = load_settings(path, {"SPOTIFY_CLIENT_ID": "from-env"})
    assert settings.services["spotify"].client_id == "from-env"
    assert settings.services["tidal"] == ServiceConfig("t", "s", "http://127.0.0.1:8888/tidal/callback")
    assert settings.country == "NL"


def test_redirect_uris_can_be_changed(tmp_path):
    settings = load_settings(tmp_path / "none.toml", {"TIDAL_REDIRECT_URI": "http://127.0.0.1:9999/cb"})
    assert settings.services["tidal"].redirect_uri == "http://127.0.0.1:9999/cb"


def test_no_file_is_fine_and_the_country_comes_from_the_locale(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_ALL", "nl_NL.UTF-8")
    settings = load_settings(tmp_path / "none.toml", {})
    assert settings.country == "NL" and not settings.is_configured("spotify")


def test_the_country_falls_back_to_us(tmp_path, monkeypatch):
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(name, raising=False)
    assert load_settings(tmp_path / "none.toml", {}).country == "US"


def test_a_broken_file_is_a_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is [not toml")
    with pytest.raises(ConfigError, match=r"config\.toml"):
        load_settings(path, {})


def test_require_explains_how_to_set_a_service_up():
    with pytest.raises(ConfigError, match="SPOTIFY_CLIENT_ID"):
        Settings().require("spotify")
    assert Settings({"tidal": ServiceConfig("id")}).require("tidal").client_id == "id"
