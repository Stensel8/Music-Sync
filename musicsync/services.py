"""Wires the settings, the token store and the service classes together."""

from .config import Settings, load_settings
from .http import ApiClient
from .oauth import ClientCredentials, OAuthClient, StoredToken, TokenStore
from .providers.base import Provider
from .providers.spotify import API as SPOTIFY_API
from .providers.spotify import SpotifyOAuth, SpotifyProvider
from .providers.tidal import API as TIDAL_API
from .providers.tidal import JSONAPI, TidalOAuth, TidalProvider


class Services:
    """Builds the OAuth client and the provider of each service. The CLI and the web interface
    both take one of these, which is also what makes them easy to test with fakes."""

    def __init__(self, settings: Settings | None = None, store: TokenStore | None = None):
        self.settings = settings or load_settings()
        self.store = store or TokenStore()

    def oauth(self, service: str) -> OAuthClient:
        config = self.settings.require(service)
        match service:
            case "spotify":
                return SpotifyOAuth(config.client_id, config.redirect_uri)
            case "tidal":
                return TidalOAuth(config.client_id, config.redirect_uri, config.client_secret)
            case _:
                raise ValueError(f"unknown service {service!r}")

    def provider(self, service: str) -> Provider:
        oauth = self.oauth(service)
        user_token = StoredToken(oauth, self.store)
        if service == "spotify":
            return SpotifyProvider(ApiClient(SPOTIFY_API, user_token))
        # Tidal: catalogue lookups need an app-level token, which needs the client secret.
        catalog = ApiClient(TIDAL_API, ClientCredentials(oauth), headers=JSONAPI) if oauth.client_secret else None
        return TidalProvider(ApiClient(TIDAL_API, user_token, headers=JSONAPI), catalog, self.settings.country)
