# Architecture

Every direction is the same three steps: read tracks from a source, find them on the target, add them to a playlist. CSV is just another source and target, so three adapters (Spotify, Tidal, CSV) cover every combination.

```
            read                     resolve                 write
 source ──────────▶  list[Track]  ──────────▶  matches  ──────────▶  target playlist
 (Spotify, Tidal, CSV)              (by id, ISRC, text)              (Spotify, Tidal, CSV)
```

## Modules (`musicsync/`)

| Module | Job |
|--------|-----|
| `models.py` | `Track`, `PlaylistInfo`, `Match`: the service-neutral shapes everything else uses. A track keeps each service's id in `ids`, so a new service needs no change here |
| `errors.py` | One hierarchy (`MusicSyncError`) for every failure we can explain; the CLI prints it, the web interface answers with its `http_status` |
| `csvio.py` | Reads and writes the CSV format, including the legacy `artist,title` files |
| `matching.py` | Scores a search result against the track we want; ISRC lookups never go through it |
| `providers/base.py` | What a service must offer (an abstract class), plus the shared `resolve()`: id, then ISRC, then text search |
| `providers/spotify.py` | Spotify Web API, following the development-mode rules since February 2026 |
| `providers/tidal.py` | Official TIDAL API v2 (JSON:API); catalogue calls use an app token when a secret is set |
| `sync.py` | `select_tracks()` and `import_tracks()`: bulk ISRC lookups per 20 tracks, de-duplication, dry run |
| `http.py` | API client: bearer token, refresh on 401, retry on 429/5xx, quota errors |
| `oauth.py` | Authorization code with PKCE, the loopback login for the CLI, the token store |
| `config.py` | `config.toml` and environment variables |
| `services.py` | `Services`: builds the OAuth client and provider of each service. The CLI and the web interface both take one, which is also how the tests swap in fakes |
| `cli.py` | The `music-sync` command |
| `web/` | The local Flask interface, on the same code |

## Decisions worth knowing

- **Official APIs only.** Both services are used through their published APIs with the user's own developer app. The unofficial Tidal library this project started on could break at any time, and Tidal's official API now covers search, ISRC lookup, playlists and the user's collection.
- **ISRC first.** It identifies a recording, so a match by ISRC is exact. Text matching (`matching.py`) is the fallback: title similarity, artist and duration, and words like *live* or *remix* must agree on both sides.
- **One token file.** `TokenStore` keeps the logins in one owner-only file that the CLI and the web interface share. The web session cookie only holds the short-lived state of a login in progress.
- **The web interface is local by design.** It listens on `127.0.0.1`, only answers to its own host names (Flask's `TRUSTED_HOSTS`) and rejects changes started by another site by checking `Sec-Fetch-Site` and `Origin`, the way Go's `http.CrossOriginProtection` does. Any website you visit could otherwise reach a server on your own machine.
- **Fewer requests.** Tidal takes 20 ISRCs per request, so lookups go in bulk. Tokens stay in memory instead of being read from the file on every request, and a refresh takes a lock so two jobs cannot spend the same one-time refresh token.
- **Long jobs run in the background** (`web/jobs.py`), with progress the page polls for.
- **Python 3.14+.** Deferred annotations, `type` aliases, `itertools.batched` and argparse's `suggest_on_error` are used as they are.

## Tests

`pytest` runs everything without network access or credentials: the services are simulated with `responses` (HTTP) and an in-memory `FakeProvider` (`tests/support.py`). What the tests cannot prove is that the real services still behave like their documentation, so `music-sync doctor` exists to check that with your own credentials.
