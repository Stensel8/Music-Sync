# Rename this to config.py and fill in your own credentials

# config.py

import os

# App specific settings
# Flask's SECRET_KEY is used for session encryption and other security features.
# In production, replace 'your_default_secret_key' with a secure random key.
SECRET_KEY = os.environ.get('SECRET_KEY', 'your_default_secret_key')


# Spotify API credentials
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID', 'your_spotify_client_id')
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', 'your_spotify_client_secret')
SPOTIFY_REDIRECT_URI = os.environ.get('SPOTIFY_REDIRECT_URI', 'http://127.0.0.1:8888/spotify/callback')

# Tidal API credentials
TIDAL_CLIENT_ID = os.environ.get('TIDAL_CLIENT_ID', 'your_tidal_client_id')
TIDAL_CLIENT_SECRET = os.environ.get('TIDAL_CLIENT_SECRET', 'your_tidal_client_secret')
TIDAL_REDIRECT_URI = os.environ.get('TIDAL_REDIRECT_URI', 'http://127.0.0.1:8888/tidal/callback')
