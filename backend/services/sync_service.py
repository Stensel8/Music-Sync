import requests

SPOTIFY_SAVED_TRACKS_URL = "https://api.spotify.com/v1/me/tracks"
TIDAL_SEARCH_URL = "https://api.tidal.com/v1/search"
TIDAL_PLAYLISTS_URL = "https://api.tidal.com/v1/users/{user_id}/playlists"
TIDAL_PLAYLIST_TRACKS_URL = "https://api.tidal.com/v1/playlists/{uuid}/tracks"
SYNC_PLAYLIST_NAME = "Music-Sync (from Spotify)"

def _spotify_headers(token):
    return {"Authorization": f"Bearer {token}"}

def _tidal_headers(token):
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

def search_tidal_track(title, artist, tidal_token, country_code):
    resp = requests.get(
        TIDAL_SEARCH_URL,
        headers=_tidal_headers(tidal_token),
        params={
            'query': f"{artist} {title}",
            'types': 'TRACKS',
            'limit': 3,
            'countryCode': country_code,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        return None
    items = resp.json().get('tracks', {}).get('items', [])
    return items[0]['id'] if items else None

def get_or_create_tidal_playlist(tidal_token, user_id, country_code):
    headers = _tidal_headers(tidal_token)
    url = TIDAL_PLAYLISTS_URL.format(user_id=user_id)

    resp = requests.get(url, headers=headers, params={'countryCode': country_code, 'limit': 50}, timeout=10)
    if resp.status_code == 200:
        for pl in resp.json().get('items', []):
            if pl.get('title') == SYNC_PLAYLIST_NAME:
                return pl['uuid']

    resp = requests.post(
        url,
        headers={**headers, 'Content-Type': 'application/json'},
        json={'title': SYNC_PLAYLIST_NAME, 'description': 'Automatisch gesynchroniseerd via Music-Sync'},
        params={'countryCode': country_code},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()['uuid']

def add_tracks_to_tidal_playlist(playlist_uuid, track_ids, tidal_token):
    if not track_ids:
        return
    resp = requests.post(
        TIDAL_PLAYLIST_TRACKS_URL.format(uuid=playlist_uuid),
        headers={**_tidal_headers(tidal_token), 'Content-Type': 'application/json'},
        json={'trackIds': track_ids, 'onArtifactNotFound': 'SKIP'},
        timeout=15,
    )
    resp.raise_for_status()

def perform_two_way_sync(spotify_token, tidal_token, tidal_user_id=None, country_code='NL', limit=200):
    if not tidal_user_id:
        return {'status': 'error', 'message': 'Tidal gebruikers-ID ontbreekt. Log opnieuw in bij Tidal.'}

    try:
        tracks = get_spotify_saved_tracks(spotify_token, limit=limit)
    except Exception as e:
        return {'status': 'error', 'message': f'Spotify opgeslagen nummers ophalen mislukt: {e}'}

    if not tracks:
        return {'status': 'success', 'message': 'Geen nummers gevonden in Spotify bibliotheek.', 'synced': 0, 'not_found': 0}

    try:
        playlist_uuid = get_or_create_tidal_playlist(tidal_token, tidal_user_id, country_code)
    except Exception as e:
        return {'status': 'error', 'message': f'Tidal playlist aanmaken mislukt: {e}'}

    synced = 0
    not_found = 0
    track_ids = []

    for track in tracks:
        try:
            tidal_id = search_tidal_track(track['title'], track['artist'], tidal_token, country_code)
            if tidal_id:
                track_ids.append(tidal_id)
                synced += 1
            else:
                not_found += 1
        except Exception:
            not_found += 1

    try:
        add_tracks_to_tidal_playlist(playlist_uuid, track_ids, tidal_token)
    except Exception as e:
        return {'status': 'error', 'message': f'Tracks toevoegen aan Tidal playlist mislukt: {e}'}

    return {
        'status': 'success',
        'message': f'{synced} nummers gesynchroniseerd naar Tidal, {not_found} niet gevonden.',
        'synced': synced,
        'not_found': not_found,
    }
