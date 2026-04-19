import os
import time
import base64
import hashlib
import requests
import secrets
from flask import Blueprint, request, render_template, session, redirect, url_for
import config

spotify_bp = Blueprint('spotify', __name__)

def generate_code_verifier():
    return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b'=').decode('utf-8')

def generate_code_challenge(verifier):
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('utf-8')

def _get(url, token):
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    resp.raise_for_status()
    return resp.json()

def fetch_spotify_user_profile(token):
    return _get("https://api.spotify.com/v1/me", token)

def fetch_spotify_user_playlists(token):
    return _get("https://api.spotify.com/v1/me/playlists?limit=50", token)

def exchange_spotify_code_for_token(code, code_verifier):
    resp = requests.post(
        'https://accounts.spotify.com/api/token',
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': config.SPOTIFY_REDIRECT_URI,
            'client_id': config.SPOTIFY_CLIENT_ID,
            'code_verifier': code_verifier,
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def refresh_spotify_token():
    refresh_token = session.get('spotify_refresh_token')
    if not refresh_token:
        return False
    try:
        resp = requests.post(
            'https://accounts.spotify.com/api/token',
            data={
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': config.SPOTIFY_CLIENT_ID,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        session['spotify_access_token'] = data['access_token']
        session['spotify_token_expires_at'] = time.time() + data.get('expires_in', 3600) - 60
        if 'refresh_token' in data:
            session['spotify_refresh_token'] = data['refresh_token']
        return True
    except Exception:
        return False

def get_valid_spotify_token():
    expires_at = session.get('spotify_token_expires_at', 0)
    if time.time() > expires_at:
        if not refresh_spotify_token():
            return None
    return session.get('spotify_access_token')

@spotify_bp.route('/login')
def login():
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)
    session['code_verifier'] = code_verifier
    state = secrets.token_urlsafe(16)
    session['auth_state'] = state

    params = {
        'response_type': 'code',
        'client_id': config.SPOTIFY_CLIENT_ID,
        'redirect_uri': config.SPOTIFY_REDIRECT_URI,
        'scope': 'user-library-read user-read-private user-read-email playlist-read-private',
        'state': state,
        'code_challenge_method': 'S256',
        'code_challenge': code_challenge,
    }
    full_url = f"https://accounts.spotify.com/authorize?{requests.compat.urlencode(params)}"
    return redirect(full_url)

@spotify_bp.route('/callback')
def callback():
    error = request.args.get('error')
    if error:
        return f"Spotify authenticatie mislukt: {error}", 400

    received_state = request.args.get('state')
    if received_state != session.get('auth_state'):
        return "State mismatch — mogelijke CSRF-aanval.", 400

    code = request.args.get('code')
    if not code:
        return "Geen authorisatiecode ontvangen.", 400

    code_verifier = session.pop('code_verifier', None)
    if not code_verifier:
        return "Code verifier ontbreekt in sessie.", 400

    try:
        token_info = exchange_spotify_code_for_token(code, code_verifier)
    except Exception as e:
        return f"Token uitwisseling mislukt: {e}", 500

    access_token = token_info.get('access_token')
    if not access_token:
        return f"Geen access token ontvangen: {token_info}", 500

    session['spotify_access_token'] = access_token
    session['spotify_refresh_token'] = token_info.get('refresh_token')
    session['spotify_token_expires_at'] = time.time() + token_info.get('expires_in', 3600) - 60
    return redirect(url_for('spotify.spotify_account'))

@spotify_bp.route('/account')
def spotify_account():
    token = get_valid_spotify_token()
    if not token:
        return redirect(url_for('spotify.login'))
    try:
        profile = fetch_spotify_user_profile(token)
        playlists_data = fetch_spotify_user_playlists(token)
    except Exception as e:
        return f"Spotify API fout: {e}", 502

    playlists = playlists_data.get('items', [])
    missing_permissions = []
    if not profile.get('email'):
        missing_permissions.append('user-read-email')
    return render_template(
        'spotify_account.html',
        account=profile,
        playlists=playlists,
        missing_permissions=missing_permissions,
        logout_url=url_for('spotify.logout'),
    )

@spotify_bp.route('/logout')
def logout():
    for key in ('spotify_access_token', 'spotify_refresh_token', 'spotify_token_expires_at'):
        session.pop(key, None)
    return redirect(url_for('login'))
