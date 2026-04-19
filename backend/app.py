import os
from flask import Flask, render_template
from backend.auth.spotify_blueprint import spotify_bp
from backend.auth.tidal_blueprint import tidal_bp
from backend.sync.sync_blueprint import sync_bp
import config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static'),
)
app.secret_key = config.SECRET_KEY

app.register_blueprint(spotify_bp, url_prefix="/spotify")
app.register_blueprint(tidal_bp, url_prefix="/tidal")
app.register_blueprint(sync_bp, url_prefix="/sync")

@app.route('/')
def dashboard():
    return render_template("dashboard.html")

@app.route('/login')
def login():
    return render_template("login.html")
