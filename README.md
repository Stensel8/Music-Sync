# Music-Sync

Syncs Spotify saved tracks to a Tidal playlist.

## Setup

```bash
git clone https://github.com/stensel8/Music-Sync
cd Music-Sync
git submodule update --init
pip install -r requirements.txt
cp sample_config.py config.py   # fill in API credentials
python run.py
```

Open `http://127.0.0.1:8888`.

## Credentials needed

- Spotify app → [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), redirect URI: `http://127.0.0.1:8888/spotify/callback`
- Tidal app → [Tidal Developer Portal](https://developer.tidal.com), redirect URI: `http://127.0.0.1:8888/tidal/callback`

## csv2tidal (submodule)

Companion CLI tool in `csv2tidal/` — reads a CSV and creates a Tidal playlist. See [csv2tidal repo](https://github.com/stensel8/csv2tidal).

**Update submodule to latest main:**

```powershell
.\scripts\update-csv2tidal-submodule.ps1
```

Add `-Push` to also push the resulting commit:

```powershell
.\scripts\update-csv2tidal-submodule.ps1 -Push
```
