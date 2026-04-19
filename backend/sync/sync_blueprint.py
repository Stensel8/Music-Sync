from flask import Blueprint, session, jsonify
from backend.services.sync_service import perform_two_way_sync

sync_bp = Blueprint('sync', __name__)

@sync_bp.route('/run', methods=['POST'])
def run_sync():
    spotify_token = session.get('spotify_access_token')
    tidal_token = session.get('tidal_access_token')

    if not spotify_token or not tidal_token:
        return jsonify({'status': 'error', 'message': 'Log eerst in bij zowel Spotify als Tidal.'}), 400

    tidal_user_id = session.get('tidal_user_id')
    country_code = session.get('tidal_country_code', 'NL')

    result = perform_two_way_sync(
        spotify_token=spotify_token,
        tidal_token=tidal_token,
        tidal_user_id=tidal_user_id,
        country_code=country_code,
    )
    status_code = 200 if result.get('status') == 'success' else 500
    return jsonify(result), status_code
