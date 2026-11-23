"""Dead man's switch: ping an external monitor after successful scans.

If the node, the service or Electrum stops working, the pings stop and the
external service (e.g. healthchecks.io, Uptime Kuma push monitor, Cronitor)
alerts. XPUB Watch itself cannot report that it is not running.
"""
import logging
import time
import urllib.request

from config import VERSION
from db import connect, get_setting, set_runtime

log = logging.getLogger("xpubwatch.heartbeat")

MIN_INTERVAL = 60  # seconds between pings, however often scans run
TIMEOUT = 10


def _valid(url):
    return url.startswith("https://") or url.startswith("http://")


def ping(url, timeout=TIMEOUT):
    """GET the URL; raises on network errors and non-2xx answers."""
    req = urllib.request.Request(url, headers={"User-Agent": "XPUB-Watch/%s" % VERSION})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.getcode()
        if code < 200 or code > 299:
            raise OSError("HTTP %d" % code)
        return code


class Heartbeat(object):
    def __init__(self, pinger=None, clock=None):
        self._ping = pinger or ping
        self._clock = clock or time.time
        self._last = 0.0
        self._last_url = None

    def after_successful_scan(self):
        """Ping when due. Never raises; the result is stored for the UI."""
        try:
            with connect() as conn:
                url = get_setting(conn, "heartbeat_url", "").strip()
        except Exception:
            log.exception("reading heartbeat setting failed")
            return False
        if not url or not _valid(url):
            self._last_url = None
            return False
        now = self._clock()
        if url == self._last_url and now - self._last < MIN_INTERVAL:
            return False
        self._last, self._last_url = now, url
        try:
            self._ping(url)
            error = None
        except Exception as exc:
            error = "Heartbeat ping failed: %s" % exc
            log.warning(error)
        try:
            with connect() as conn:
                set_runtime(conn, "heartbeat_error", error)
                if error is None:
                    set_runtime(conn, "heartbeat_ok_at", int(now))
        except Exception:
            log.exception("storing heartbeat state failed")
        return error is None
