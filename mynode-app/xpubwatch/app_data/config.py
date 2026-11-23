import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _flag(name, default):
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def _read_version():
    try:
        with open(os.path.join(_HERE, "VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "unknown"


VERSION = _read_version()
DATA_DIR = os.environ.get("XPUBWATCH_DATA_DIR", "/mnt/hdd/mynode/xpubwatch")
DB_PATH = os.environ.get("XPUBWATCH_DB", os.path.join(DATA_DIR, "xpubwatch.db"))
ELECTRUM_HOST = os.environ.get("XPUBWATCH_ELECTRUM_HOST", "127.0.0.1")
ELECTRUM_PORT = int(os.environ.get("XPUBWATCH_ELECTRUM_PORT", "50001"))
ELECTRUM_TIMEOUT = float(os.environ.get("XPUBWATCH_ELECTRUM_TIMEOUT", "30"))
POLL_SECONDS = int(os.environ.get("XPUBWATCH_POLL_SECONDS", "15"))
LOOKAHEAD = int(os.environ.get("XPUBWATCH_LOOKAHEAD", "20"))
WEB_HOST = os.environ.get("XPUBWATCH_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("XPUBWATCH_WEB_PORT", "5114"))
START_WATCHER = _flag("XPUBWATCH_START_WATCHER", "1")
# The UI is served by MyNode's nginx over HTTPS; the session cookie is therefore Secure.
COOKIE_SECURE = _flag("XPUBWATCH_COOKIE_SECURE", "1")
SESSION_HOURS = int(os.environ.get("XPUBWATCH_SESSION_HOURS", "12"))
