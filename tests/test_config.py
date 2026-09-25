import os

import pytest

from musicsync.config import TEMPLATE, ServiceConfig, Settings, config_path, ensure_config_file, load_settings
from musicsync.errors import ConfigError
from musicsync.services import Services


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


def test_the_template_is_valid_and_fills_in_what_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    untouched = load_settings(TEMPLATE, {})
    assert not untouched.is_configured("spotify") and not untouched.is_configured("tidal")
    assert untouched.country == "DE"  # the template does not pick a country for you
    filled = tmp_path / "config.toml"
    filled.write_text(
        TEMPLATE.read_text(encoding="utf-8")
        .replace('client_id = ""', 'client_id = "ID"')
        .replace('client_secret = ""', 'client_secret = "SECRET"'),
        encoding="utf-8",
    )
    settings = load_settings(filled, {})
    assert settings.services["spotify"].client_id == "ID"
    assert settings.services["tidal"].client_id == "ID" and settings.services["tidal"].client_secret == "SECRET"


def test_the_first_run_makes_the_settings_file_from_the_template():
    assert ensure_config_file() is True
    assert config_path().read_text(encoding="utf-8") == TEMPLATE.read_text(encoding="utf-8")
    if os.name != "nt":
        assert config_path().stat().st_mode & 0o777 == 0o600  # it is going to hold a secret
    config_path().write_text("mine", encoding="utf-8")
    assert ensure_config_file() is False
    assert config_path().read_text(encoding="utf-8") == "mine"  # never overwritten


def test_a_config_folder_that_cannot_be_made_is_not_an_error(tmp_path):
    (tmp_path / "config").write_text("a file where the config folder should go", encoding="utf-8")
    assert ensure_config_file() is False


def test_a_running_app_picks_up_changes_to_the_settings_file():
    services = Services()
    assert not services.settings.is_configured("tidal")
    config_path().parent.mkdir(parents=True)
    config_path().write_text('[tidal]\nclient_id = "ID"\n', encoding="utf-8")
    assert services.settings.is_configured("tidal")
    config_path().write_text('[tidal]\nclient_id = ""\n', encoding="utf-8")
    os.utime(config_path(), ns=(1, 1))  # a coarse file system clock must not hide the change
    assert not services.settings.is_configured("tidal")


def test_require_explains_how_to_set_a_service_up():
    with pytest.raises(ConfigError, match=r"developer\.spotify\.com/dashboard.*SPOTIFY_CLIENT_ID"):
        Settings().require("spotify")
    assert Settings({"tidal": ServiceConfig("id")}).require("tidal").client_id == "id"
