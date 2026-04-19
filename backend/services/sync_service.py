import requests
import tidalapi

SPOTIFY_SAVED_TRACKS_URL = "https://api.spotify.com/v1/me/tracks"
SYNC_PLAYLIST_NAME = "Music-Sync (from Spotify)"

def _spotify_headers(token):
    return {"Authorization": f"Bearer {token}"}

def get_spotify_saved_tracks(spotify_token, limit=200):
    headers = _spotify_headers(spotify_token)
    tracks = []
    url = f"{SPOTIFY_SAVED_TRACKS_URL}?limit=50"
    while url and len(tracks) < limit:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get('items', []):
            track = item.get('track') or {}
            artists = track.get('artists') or []
            if track.get('name') and artists:
                tracks.append({
                    'title': track['name'],
                    'artist': artists[0]['name'],
                })
        url = data.get('next')
    return tracks[:limit]

def _tidal_session(access_token, refresh_token):
    session = tidalapi.Session()
    session.load_oauth_session('Bearer', access_token, refresh_token)
    return session

def _get_or_create_playlist(session, name):
    for pl in session.user.playlists():
        if pl.name == name:
            return pl
    return session.user.create_playlist(name, 'Automatisch gesynchroniseerd via Music-Sync')

def perform_two_way_sync(spotify_token, tidal_token, tidal_refresh_token,
                         tidal_user_id=None, country_code='NL', limit=200):
    if not tidal_user_id:
        return {'status': 'error', 'message': 'Tidal gebruikers-ID ontbreekt. Log opnieuw in bij Tidal.'}

    try:
        tracks = get_spotify_saved_tracks(spotify_token, limit=limit)
    except Exception as e:
        return {'status': 'error', 'message': f'Spotify opgeslagen nummers ophalen mislukt: {e}'}

    if not tracks:
        return {'status': 'success', 'message': 'Geen nummers gevonden in Spotify bibliotheek.', 'synced': 0, 'not_found': 0}

    try:
        tidal = _tidal_session(tidal_token, tidal_refresh_token)
        if not tidal.check_login():
            return {'status': 'error', 'message': 'Tidal sessie verlopen. Log opnieuw in.'}
    except Exception as e:
        return {'status': 'error', 'message': f'Tidal verbinding mislukt: {e}'}

    try:
        playlist = _get_or_create_playlist(tidal, SYNC_PLAYLIST_NAME)
    except Exception as e:
        return {'status': 'error', 'message': f'Tidal playlist aanmaken mislukt: {e}'}

    synced = 0
    not_found = 0
    track_ids = []

    for track in tracks:
        try:
            results = tidal.search(f"{track['artist']} {track['title']}", models=[tidalapi.Track], limit=3)
            hits = results.get('tracks', [])
            if hits:
                track_ids.append(str(hits[0].id))
                synced += 1
            else:
                not_found += 1
        except Exception:
            not_found += 1

    try:
        if track_ids:
            playlist.add(track_ids)
    except Exception as e:
        return {'status': 'error', 'message': f'Tracks toevoegen aan Tidal playlist mislukt: {e}'}

    return {
        'status': 'success',
        'message': f'{synced} nummers gesynchroniseerd naar Tidal, {not_found} niet gevonden.',
        'synced': synced,
        'not_found': not_found,
    }
