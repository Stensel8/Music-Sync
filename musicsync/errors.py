"""Failures we can explain to the user. The CLI prints them, the web interface answers with ``http_status``."""


class MusicSyncError(Exception):
    """A problem whose message is safe to show to the user. Anything else is a bug and fails loudly."""

    http_status = 400  # what the web interface answers with


class ConfigError(MusicSyncError):
    """The setup is incomplete or unreadable."""


class CsvError(MusicSyncError):
    """A CSV file could not be read."""


class ProviderError(MusicSyncError):
    """A service cannot do what was asked, for example reading a playlist that is not yours."""


class LoginError(MusicSyncError):
    """Logging in to a service failed."""


class NotLoggedIn(LoginError):
    """There is no valid login for the service (yet, or any more)."""

    http_status = 401


class NetworkError(MusicSyncError):
    """A service could not be reached."""

    http_status = 502


class ApiError(MusicSyncError):
    """A service answered with an error status."""

    http_status = 502

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class QuotaExceeded(ApiError):
    """The app has used up its quota; retrying will not help."""
