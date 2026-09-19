import os

DATA_DIR = os.environ.get("XPUBWATCH_DATA_DIR", "/mnt/hdd/mynode/xpubwatch")
DB_PATH = os.environ.get("XPUBWATCH_DB", os.path.join(DATA_DIR, "xpubwatch.db"))
ELECTRUM_HOST = os.environ.get("XPUBWATCH_ELECTRUM_HOST", "127.0.0.1")
ELECTRUM_PORT = int(os.environ.get("XPUBWATCH_ELECTRUM_PORT", "50001"))
POLL_SECONDS = int(os.environ.get("XPUBWATCH_POLL_SECONDS", "15"))
LOOKAHEAD = int(os.environ.get("XPUBWATCH_LOOKAHEAD", "20"))
WEB_HOST = os.environ.get("XPUBWATCH_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("XPUBWATCH_WEB_PORT", "5115"))
