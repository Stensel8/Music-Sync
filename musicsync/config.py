"""Settings: environment variables override the optional ``config.toml``."""

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

SERVICES = ("spotify", "tidal")


def config_dir() -> Path:
    """Where Music-Sync keeps its configuration and login tokens."""
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "music-sync"


def _locale_country() -> str:
    """The country of the system locale ("nl_NL.UTF-8" gives "NL"); a sensible default for Tidal."""
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
    country: str = "US"  # ISO 3166-1 alpha-2: decides which Tidal catalogue is searched

    def is_configured(self, service: str) -> bool:
        return bool(self.services.get(service, ServiceConfig()).client_id)

    def require(self, service: str) -> ServiceConfig:
        """The credentials of ``service``, or a ConfigError that explains how to set them up."""
        if not self.is_configured(service):
            raise ConfigError(
                f"{service.title()} client ID missing. Set {service.upper()}_CLIENT_ID or add "
                f"client_id under [{service}] in {config_dir() / 'config.toml'} (see the README)."
            )
        return self.services[service]


def load_settings(path: Path | None = None, env: Mapping[str, str] | None = None) -> Settings:
    """Read ``config.toml`` (if there is one) and let environment variables override it."""
    env = os.environ if env is None else env
    path = path or config_dir() / "config.toml"
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc

    services = {}
    for name in SERVICES:
        section, prefix = data.get(name, {}), name.upper()
        services[name] = ServiceConfig(
            client_id=env.get(f"{prefix}_CLIENT_ID") or section.get("client_id", ""),
            client_secret=env.get(f"{prefix}_CLIENT_SECRET") or section.get("client_secret", ""),
            # The web interface listens on 8888; the CLI login borrows the same address.
            redirect_uri=env.get(f"{prefix}_REDIRECT_URI")
            or section.get("redirect_uri")
            or f"http://127.0.0.1:8888/{name}/callback",
        )
    country = env.get("MUSICSYNC_COUNTRY") or data.get("country") or _locale_country()
    return Settings(services, country.upper())
