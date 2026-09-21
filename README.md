# Music-Sync

Move liked songs and playlists between Spotify, Tidal and CSV files. Use it from the command line, or from a small web page on your own computer.

| From \ to | CSV | Spotify | Tidal |
|-----------|-----|---------|-------|
| **Spotify** | `export` | | `transfer` |
| **Tidal** | `export` | `transfer` | |
| **CSV** | | `import` | `import` |

- Tracks are matched by ISRC when there is one, otherwise by title, artist and length. A live version is never taken for the studio version.
- Running a command twice does not add tracks twice.
- Tracks that cannot be found are listed, and can be saved to a CSV file.
- `--dry-run` looks everything up and changes nothing.

Music-Sync only works with playlist data such as titles, artists and ISRCs. It never touches audio.

## Installation

You need Python 3.14 or newer.

```bash
git clone https://github.com/Stensel8/Music-Sync
cd Music-Sync
python3 -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e '.[web]'                           # leave out [web] for the command line only
```

## Setup

Music-Sync needs a developer app for each service, made from your own account. There is no shared app.

### Spotify

> [!IMPORTANT]
> Spotify's API needs **Premium**. Since February 2026 an app only works when its owner has an active Premium subscription. Without it the dashboard shows this banner, and every request fails with HTTP 403:
>
> ![The Spotify for Developers dashboard with the banner "Your application is blocked from accessing the Web API since you do not have a Spotify Premium subscription."](docs/images/spotify-premium-required.avif)
>
> On a Free account you cannot even select the Web API when you create the app:
>
> ![The "Create app" page on a Free account. A banner says "Upgrade to Spotify Premium to access the Web API", and the Web API option is greyed out.](docs/images/spotify-create-app-free.avif)
>
> Without Premium you can ask someone who has it to create the app and add you under *Settings > User Management* (up to 5 people). Spotify only requires Premium of the owner. Or leave Spotify out: Tidal and CSV work without it.
>
> Sources: [development mode requirements](https://developer.spotify.com/documentation/web-api/concepts/quota-modes) and the [February 2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).

1. Create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Use the redirect URI `http://127.0.0.1:8888/spotify/callback` (`127.0.0.1`, not `localhost`).
3. Under *Which API/SDKs are you planning to use?* tick **Web API** and nothing else.
4. Copy the Client ID. You do not need the client secret.

![Only Web API is ticked.](docs/images/spotify-web-api-ticked.avif)

### Tidal

1. Open the [TIDAL developer dashboard](https://developer.tidal.com/dashboard), where you register apps, and click *Create New App*.
2. Use the redirect URI `http://127.0.0.1:8888/tidal/callback`.
3. Allow the scopes `collection.read`, `collection.write`, `playlists.read`, `playlists.write` and `user.read`.
4. Copy the Client ID and the Client Secret. The secret is used to search Tidal's catalogue. Without it Music-Sync searches with your own login, which Tidal may refuse.

![The TIDAL developer dashboard with the "Create New App" button and one app.](docs/images/tidal-dashboard.avif)

![The Scopes panel of a TIDAL app: collection.read, collection.write, playlists.read, playlists.write and user.read.](docs/images/tidal-scopes.avif)

### Settings file

Music-Sync creates the settings file for you the first time you run any command, for example `music-sync status`, and prints where it is. It goes in your user profile, outside the project:

- Linux and macOS: `~/.config/music-sync/config.toml`
- Windows: `%APPDATA%\music-sync\config.toml`

`.config` is a hidden folder, so your editor's file tree does not show it. Open the file by its path.

Fill in what you copied:

```toml
[spotify]
client_id = "..."

[tidal]
client_id = "..."
client_secret = "..."
```

Only you can read this file, and it is never part of the repository. Music-Sync reads it again whenever it changes, so you never need to restart. `music-sync status` shows the path again.

An optional `country = "NL"` at the top picks the Tidal catalogue. Without it, your system's country is used. Environment variables take priority over the file: `SPOTIFY_CLIENT_ID`, `TIDAL_CLIENT_ID`, `TIDAL_CLIENT_SECRET` and `MUSICSYNC_COUNTRY`. If you use other redirect URIs, set `SPOTIFY_REDIRECT_URI` and `TIDAL_REDIRECT_URI` to match.

### Log in

```bash
music-sync login spotify
music-sync login tidal
music-sync doctor          # tries the real APIs and shows what does not work
```

Your logins are kept in `tokens.json`, in the same folder. Keep that file private: it gives access to your accounts.

## Usage

```bash
# Spotify or Tidal to CSV
music-sync export spotify --liked -o liked.csv
music-sync export spotify --playlist "Road trip" -o road-trip.csv
music-sync export tidal --all -o backup/tidal          # liked songs and one CSV per playlist

# CSV to Spotify or Tidal
music-sync import tidal liked.csv --playlist "From CSV" --unmatched missing.csv
music-sync import spotify liked.csv --dry-run

# from one service straight to the other
music-sync transfer spotify tidal --liked
music-sync transfer tidal spotify --playlist "Road trip" --to-playlist "Road trip (Tidal)"

music-sync playlists tidal          # list playlists with their ids
music-sync status                   # what is set up and logged in
```

`--min-score 0.8` sets how sure a text match must be. Lower it to accept doubtful matches, raise it to be stricter. Put `-v` before the command (`music-sync -v transfer ...`) to log every API call. That helps in a bug report.

## CSV format

Music-Sync writes a header row with these columns. Only `title` is needed when reading.

| Column | Contents |
|--------|----------|
| `title` | Track title |
| `artists` | Artists, separated by `;` |
| `album` | Album title |
| `duration_ms` | Length in milliseconds |
| `isrc` | ISRC, the most reliable way to find a track again |
| `spotify_uri` | For example `spotify:track:...`, in files exported from Spotify |
| `tidal_id` | Tidal track id, in files exported from Tidal |

Files must be UTF-8 (the BOM from Excel is fine). The column names of other exporters are understood too (`Track Name`, `Artist Name(s)`, `Duration (ms)`, `Track URI`). A file without a header row is read as `artist,title`.

## Web interface

```bash
music-sync web              # then open http://127.0.0.1:8888
```

Export, import and transfer in a browser, with progress for long jobs. It uses the same logins as the command line.

It is meant for one person on their own computer. It only listens on `127.0.0.1` and refuses requests that another website started, so never expose it to a network. The port must match your redirect URIs. `--port` changes it.

## Good to know

- Spotify only lists playlists you own or collaborate on. Playlists you follow, including Spotify's own, cannot be read since the API changes of February 2026. Music-Sync lists them and skips them.
- Tidal looks up 20 ISRCs per request and Spotify one, so a transfer to Tidal is fast and a transfer to Spotify takes a request per track. In development mode Spotify also returns at most 10 search results.
- Empty playlists are not exported or transferred. Music-Sync says which ones it skipped.
- Local files in a Spotify playlist have no ID or ISRC. They are exported, and on import they are matched by text only.
- Tidal's API is young. If something fails, run `music-sync doctor tidal` and add its output to your issue.

## Development

```bash
pip install -e . --group dev
pytest
ruff check . && ruff format --check . && pyright
vulture                            # unused code
bandit -r musicsync -ll            # security scan
```

CI runs the same checks, plus `pip-audit` for known vulnerabilities in the dependencies.

## License

[AGPL-3.0](LICENSE).

## Credits

Music-Sync grew out of csv2tidal, started by [Nugman](https://github.com/Nugman) and roland.behme, who also chose the license. Their script was inspired by [RZetko](https://gist.github.com/RZetko/71801a20188e842ef03bed3b6d7a297f) and used login code from [spotify_to_tidal](https://github.com/timrae/spotify_to_tidal) by [Tim Rae](https://github.com/timrae).
