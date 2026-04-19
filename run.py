import sys
from pathlib import Path

if not Path("config.py").exists():
    sys.exit(
        "ERROR: config.py not found.\n"
        "Run: cp sample_config.py config.py\n"
        "Then fill in your API credentials."
    )

from backend.app import app

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8888, debug=True)
