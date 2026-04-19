# Music-Sync

Syncs your Spotify saved tracks to a Tidal playlist.

## Requirements

- Python 3.12+
- Spotify Developer App (OAuth2 with PKCE)
- Tidal Developer App (OAuth2 with PKCE)

## Setup

```bash
pip install -r requirements.txt
cp sample_config.py config.py
# Fill in your API credentials in config.py
```

## Running

```bash
python run.py
```

Open `http://127.0.0.1:8888` in your browser.

## Flow

1. Login with Spotify → `/spotify/login`
2. Login with Tidal → `/tidal/login`
3. Press **Start Sync** on the dashboard
4. Your Spotify saved tracks are searched on Tidal and added to a playlist called `Music-Sync (from Spotify)`

## Config

Copy `sample_config.py` to `config.py` and fill in:

| Key | Description |
|-----|-------------|
| `SECRET_KEY` | Flask session secret (use a strong random string in production) |
| `SPOTIFY_CLIENT_ID` | From [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) |
| `SPOTIFY_CLIENT_SECRET` | From Spotify Developer Dashboard |
| `SPOTIFY_REDIRECT_URI` | Must match dashboard — default: `http://127.0.0.1:8888/spotify/callback` |
| `TIDAL_CLIENT_ID` | From [Tidal Developer Portal](https://developer.tidal.com) |
| `TIDAL_CLIENT_SECRET` | From Tidal Developer Portal |
| `TIDAL_REDIRECT_URI` | Must match portal — default: `http://127.0.0.1:8888/tidal/callback` |

## Tech Stack

- Python 3.12+ / Flask
- Tailwind CSS + DaisyUI (via CDN)
- Spotify Web API + Tidal API v1
