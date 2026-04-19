import os
import time
import requests
import hashlib
import base64
import secrets
from flask import Blueprint, request, render_template, session, redirect, url_for
import config

tidal_bp = Blueprint('tidal', __name__)

AUTH_URL = "https://auth.tidal.com/v1/oauth2/authorize"
TOKEN_URL = "https://auth.tidal.com/v1/oauth2/token"
API_BASE = "https://api.tidal.com/v1"
SCOPE = "r_usr w_usr w_sub"

def generate_pkce_pair():
    code_verifier = base64.urlsafe_b64encode(os.urandom(40)).decode('utf-8').rstrip('=')
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode('utf-8')).digest()
    ).decode('utf-8').rstrip('=')
    return code_verifier, code_challenge

def refresh_tidal_token():
    refresh_token = session.get('tidal_refresh_token')
    if not refresh_token:
        return False
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                'client_id': config.TIDAL_CLIENT_ID,
                'client_secret': config.TIDAL_CLIENT_SECRET,
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        session['tidal_access_token'] = data['access_token']
        session['tidal_token_expires_at'] = time.time() + data.get('expires_in', 604800) - 60
        if 'refresh_token' in data:
            session['tidal_refresh_token'] = data['refresh_token']
        return True
    except Exception:
        return False

def get_valid_tidal_token():
    expires_at = session.get('tidal_token_expires_at', 0)
    if time.time() > expires_at:
        if not refresh_tidal_token():
            return None
    return session.get('tidal_access_token')

def get_tidal_user_profile(token):
    resp = requests.get(
        f"{API_BASE}/users/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()

def get_tidal_user_playlists(token, user_id, country_code='NL'):
    resp = requests.get(
        f"{API_BASE}/users/{user_id}/playlists",
        headers={"Authorization": f"Bearer {token}"},
        params={'countryCode': country_code, 'limit': 50},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get('items', [])

@tidal_bp.route('/login')
def tidal_login():
    code_verifier, code_challenge = generate_pkce_pair()
    session['tidal_code_verifier'] = code_verifier
    state = secrets.token_urlsafe(16)
    session['tidal_auth_state'] = state

    params = {
        'response_type': 'code',
        'client_id': config.TIDAL_CLIENT_ID,
        'redirect_uri': config.TIDAL_REDIRECT_URI,
        'scope': SCOPE,
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256',
    }
    return redirect(f"{AUTH_URL}?{requests.compat.urlencode(params)}")

@tidal_bp.route('/callback')
def tidal_callback():
    error = request.args.get('error')
    if error:
        return f"Tidal authenticatie mislukt: {error}", 400

    if request.args.get('state') != session.get('tidal_auth_state'):
        return "State mismatch — mogelijke CSRF-aanval.", 400

    code = request.args.get('code')
    code_verifier = session.pop('tidal_code_verifier', None)
    if not code or not code_verifier:
        return "Authorisatiecode of verifier ontbreekt.", 400

    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                'client_id': config.TIDAL_CLIENT_ID,
                'client_secret': config.TIDAL_CLIENT_SECRET,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': config.TIDAL_REDIRECT_URI,
                'code_verifier': code_verifier,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10,
        )
        resp.raise_for_status()
        token_info = resp.json()
    except Exception as e:
        return f"Token uitwisseling mislukt: {e}", 500

    access_token = token_info.get('access_token')
    if not access_token:
        return f"Geen access token ontvangen: {token_info}", 500

    session['tidal_access_token'] = access_token
    session['tidal_refresh_token'] = token_info.get('refresh_token')
    session['tidal_token_expires_at'] = time.time() + token_info.get('expires_in', 604800) - 60

    try:
        profile = get_tidal_user_profile(access_token)
        session['tidal_user_id'] = profile.get('id')
        session['tidal_country_code'] = profile.get('countryCode', 'NL')
    except Exception:
        pass

    return redirect(url_for('tidal.tidal_account'))

@tidal_bp.route('/account')
def tidal_account():
    token = get_valid_tidal_token()
    if not token:
        return redirect(url_for('tidal.tidal_login'))

    user_id = session.get('tidal_user_id')
    country_code = session.get('tidal_country_code', 'NL')

    try:
        profile = get_tidal_user_profile(token)
        playlists = get_tidal_user_playlists(token, user_id, country_code) if user_id else []
    except Exception as e:
        return f"Tidal API fout: {e}", 502

    return render_template(
        'tidal_account.html',
        tidal_profile=profile,
        tidal_playlists=playlists,
        logout_url=url_for('tidal.tidal_logout'),
    )

@tidal_bp.route('/logout')
def tidal_logout():
    for key in ('tidal_access_token', 'tidal_refresh_token', 'tidal_token_expires_at',
                'tidal_user_id', 'tidal_country_code'):
        session.pop(key, None)
    return redirect(url_for('login'))
