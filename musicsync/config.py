"""Settings from ``config.toml`` in the user's config folder. Environment variables override the file."""

import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

SERVICES = ("spotify", "tidal")
# Where each service's developer apps are made.
DEVELOPER_DASHBOARDS = {
    "spotify": "https://developer.spotify.com/dashboard",
    "tidal": "https://developer.tidal.com/dashboard",
}
TEMPLATE = Path(__file__).with_name("config_template.toml")


def config_dir() -> Path:
    """Where Music-Sync keeps its settings and logins."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "music-sync"


def config_path() -> Path:
    return config_dir() / "config.toml"


def write_private(path: Path, text: str, *, exclusive: bool = False) -> None:
    """Write a file that only the current user can read, created that way from the start. A file that is
    already there is replaced in one step, or with ``exclusive`` left alone (FileExistsError)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        fd, tmp = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), None
    else:
        # A new file of our own (mkstemp makes it 0600) that takes the place of the old one: a crash cannot
        # leave half a file, and what was there before, however readable, has no say in the permissions.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if tmp:
            Path(tmp).replace(path)
    except BaseException:
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        raise


def ensure_config_file() -> bool:
    """Create ``config.toml`` from the template when it is missing. True when it was just created."""
    if config_path().exists():
        return False
    try:
        write_private(config_path(), TEMPLATE.read_text(encoding="utf-8"), exclusive=True)
    except OSError:
        return False  # a folder we cannot write to: carry on without a file
    return True


def default_redirect_uri(service: str) -> str:
    """Where a service sends you back after logging in, unless the settings say otherwise."""
    return f"http://127.0.0.1:8888/{service}/callback"


def _locale_country() -> str:
    """The country of the system locale ("nl_NL.UTF-8" gives "NL"), else "US"."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if match := re.match(r"[a-z]{2,3}_([A-Z]{2})", os.environ.get(var, "")):
            return match.group(1)
    return "US"


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    """The developer-app credentials of one service."""

    client_id: str = ""
    client_secret: str = ""  # only Tidal uses it (for catalogue lookups); Spotify works with PKCE alone
    redirect_uri: str = ""


@dataclass(frozen=True, slots=True)
class Settings:
    services: Mapping[str, ServiceConfig] = field(default_factory=dict)
    country: str = "US"  # decides which Tidal catalogue is searched

    def is_configured(self, service: str) -> bool:
        return bool(self.services.get(service, ServiceConfig()).client_id)

    def redirect_uri(self, service: str) -> str:
        return self.services.get(service, ServiceConfig()).redirect_uri or default_redirect_uri(service)

    def require(self, service: str) -> ServiceConfig:
        """The credentials of ``service``, or a ConfigError that says how to set them."""
        if not self.is_configured(service):
            raise ConfigError(
                f"{service.title()} client ID missing. Create an app at {DEVELOPER_DASHBOARDS[service]} and put "
                f"its Client ID under [{service}] in {config_path()}, or set {service.upper()}_CLIENT_ID "
                "(see Setup in the README)."
            )
        return self.services[service]


def load_settings(path: Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """Read ``config.toml`` (if there is one) and let environment variables override it."""
    env = os.environ if env is None else env
    path = path or config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    def text(table: dict, key: str, where: str = "") -> str:
        """A setting, which has to be text: a number or a list there is a slip worth explaining."""
        if not isinstance(value := table.get(key, ""), str):
            raise ConfigError(f'{key}{where} in {path} must be text between quotes, like {key} = "..."')
        return value

    services = {}
    for name in SERVICES:
        section, prefix, where = data.get(name, {}), name.upper(), f" under [{name}]"
        if not isinstance(section, dict):  # spotify = "..." instead of a [spotify] section
            raise ConfigError(f"{name} in {path} must be a section: [{name}] on a line, its settings under it")
        services[name] = ServiceConfig(
            client_id=env.get(f"{prefix}_CLIENT_ID") or text(section, "client_id", where),
            client_secret=env.get(f"{prefix}_CLIENT_SECRET") or text(section, "client_secret", where),
            # The web interface listens on 8888; the CLI login borrows the same address.
            redirect_uri=env.get(f"{prefix}_REDIRECT_URI")
            or text(section, "redirect_uri", where)
            or default_redirect_uri(name),
        )
    country = env.get("MUSICSYNC_COUNTRY") or text(data, "country") or _locale_country()
    return Settings(services, country.upper())
