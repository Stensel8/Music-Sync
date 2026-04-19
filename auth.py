#!/usr/bin/env python3

from pathlib import Path
import tidalapi

SESSION_FILE = Path(".tidal-session.json")

def open_tidal_session() -> tidalapi.Session:
    session = tidalapi.Session()
    session.login_session_file(SESSION_FILE)
    return session
