# Music-Sync

Move your music between **Spotify**, **Tidal** and **CSV**: liked songs and playlists, in any direction. It is a command line tool with an optional local web interface.

| From \ to | CSV | Spotify | Tidal |
|-----------|-----|---------|-------|
| **Spotify** | `export` | | `transfer` |
| **Tidal** | `export` | `transfer` | |
| **CSV** | | `import` | `import` |

- **Accurate matching.** Tracks are found by their ISRC (the unique code of a recording) whenever there is one. The rest is matched on title, artist and duration, and a live version is never mistaken for the studio one.
- **Safe to run twice.** Tracks that are already in the playlist are skipped, so nothing is added twice.
- **Nothing lost silently.** Tracks that cannot be found are listed, and can be saved to a CSV of their own.
- **Preview first.** `--dry-run` looks everything up and changes nothing.

Music-Sync only handles playlist metadata (titles, artists, ISRC and so on). It never downloads, records or decrypts audio. A CSV export is plain data you can take to any other tool you like.

## Installation

You need Python 3.14 or newer.

```bash
git clone https://github.com/Stensel8/Music-Sync
cd Music-Sync
python3 -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e '.[web]'                           # leave out [web] if you only want the command line
```

## Setup

Each user registers their own (free) developer apps. There is no shared app because Spotify limits apps in development mode to 5 users and, since February 2026, requires the app's owner to have Spotify Premium.

**1. Spotify**: create an app in the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) with the redirect URI `http://127.0.0.1:8888/spotify/callback` (use `127.0.0.1`, not `localhost`). Copy the Client ID.

**2. Tidal**: create an app in the [TIDAL Developer Portal](https://developer.tidal.com) with the redirect URI `http://127.0.0.1:8888/tidal/callback`. Copy the Client ID and the Client Secret. The secret is used for looking tracks up in Tidal's catalogue (search, ISRC). Without it Music-Sync falls back to your own login, which Tidal may refuse for those calls.

**3. Configuration**: put the values in `~/.config/music-sync/config.toml` (on Windows `%APPDATA%\music-sync\config.toml`):

```toml
country = "NL"          # decides which Tidal catalogue is searched; defaults to your system locale

[spotify]
client_id = "..."

[tidal]
client_id = "..."
client_secret = "..."
```

Environment variables override the file: `SPOTIFY_CLIENT_ID`, `TIDAL_CLIENT_ID`, `TIDAL_CLIENT_SECRET`, `MUSICSYNC_COUNTRY`. If you register other redirect URIs, set `SPOTIFY_REDIRECT_URI` and `TIDAL_REDIRECT_URI` to match.

**4. Log in and check**:

```bash
music-sync login spotify
music-sync login tidal
music-sync doctor          # tries the real APIs and says exactly what does not work
```

Your logins are stored in `~/.config/music-sync/tokens.json`, readable by you only. Keep that file secret: it gives access to your accounts.

## Usage

```bash
# Spotify or Tidal to CSV
music-sync export spotify --liked -o liked.csv
music-sync export spotify --playlist "Road trip" -o road-trip.csv
music-sync export tidal --all -o backup/tidal          # liked songs plus one CSV per playlist

# CSV to Spotify or Tidal
music-sync import tidal liked.csv --playlist "From CSV" --unmatched missing.csv
music-sync import spotify liked.csv --dry-run

# straight from one service to the other
music-sync transfer spotify tidal --liked
music-sync transfer tidal spotify --playlist "Road trip" --to-playlist "Road trip (Tidal)"

music-sync playlists tidal          # list playlists with their ids
music-sync status                   # what is configured and logged in
```

`--min-score 0.8` sets how sure a text match must be. Lower it to accept more doubtful matches, raise it to be stricter. Put `-v` in front of any command (`music-sync -v transfer ...`) to log every API call, which helps a bug report.

## CSV format

Music-Sync writes a header row and these columns. Only `title` is required when reading.

| Column | Contents |
|--------|----------|
| `title` | Track title |
| `artists` | Artists, separated by `;` |
| `album` | Album title |
| `duration_ms` | Length in milliseconds |
| `isrc` | ISRC, the most reliable way to find a track again |
| `spotify_uri` | e.g. `spotify:track:...`, present on files exported from Spotify |
| `tidal_id` | Tidal track id, present on files exported from Tidal |

Files must be UTF-8 (a BOM from Excel is fine). Reading also accepts the column names other exporters use (`Track Name`, `Artist Name(s)`, `Duration (ms)`, `Track URI`) and the original csv2tidal format: no header row, just `artist,title`.

## Web interface

```bash
music-sync web              # then open http://127.0.0.1:8888
```

The same export, import and transfer in a browser, with progress for long jobs. It uses the same logins as the command line. It is meant for one user on their own machine: it listens on `127.0.0.1` only, answers to no other host name, and refuses changes that another website tried to start (it checks the `Sec-Fetch-Site` and `Origin` headers that browsers set, so no tokens are needed). Do not expose it to a network. The port must match the redirect URIs you registered (`--port` changes it).

## Good to know

- **Spotify only shows playlists you own or collaborate on.** Playlists you merely follow, including Spotify's own, cannot be exported since the API changes of February 2026. Music-Sync lists them and skips them.
- **Tidal looks up 20 ISRCs per request, Spotify one.** A transfer *to* Tidal is therefore fast, while one *to* Spotify takes a request per track. Spotify search also returns at most 10 results per request for apps in development mode.
- **Local files** in a Spotify playlist have no ID or ISRC. They are exported, and on import they are matched by text only.
- **Tidal's API is young.** If something fails, run `music-sync doctor tidal` and include its output in an issue.

## Coming from csv2tidal?

This project started as `csv2tidal` (a small CSV-to-Tidal script) and absorbed an earlier Flask prototype that was also called Music-Sync. Everything both could do is still here:

| Before | Now |
|--------|-----|
| `python3 csv2tidal.py file.csv` | `music-sync import tidal file.csv --playlist NAME` |
| Flask app, "Sync Spotify to Tidal" | `music-sync web`, or `music-sync transfer spotify tidal --liked` |
| `config.py` copied from `sample_config.py` | `config.toml` or environment variables |

The `artist,title` CSV files from csv2tidal still work as they are. One thing is different: Tidal login now goes through Tidal's official API with your own developer app, instead of the shared login of the unofficial tidalapi library. The old `.tidal-session.json` and `.session.yml` files are no longer used. They contain login tokens, so delete them.

## Development

```bash
pip install -e . --group dev       # the project, Flask, pytest, ruff and pyright
pytest
ruff check . && ruff format --check . && pyright
```

See [docs/architecture.md](docs/architecture.md) for how the code is laid out.

## License and credits

[AGPL-3.0](LICENSE). csv2tidal was inspired by [RZetko's gist](https://gist.github.com/RZetko/71801a20188e842ef03bed3b6d7a297f). The tidalapi-based login in its earlier versions was adapted from [spotify_to_tidal](https://github.com/spotify2tidal/spotify_to_tidal); that code has since been replaced.
