import json
import socket
import threading
import time


class ElectrumError(RuntimeError):
    pass


class ElectrumClient:
    """Small synchronous Electrum JSON-RPC client for a trusted local electrs.

    Each RPC uses a fresh local TCP connection. Transient electrs stalls can happen
    while the indexer is busy, so calls are retried before the watcher is marked
    unhealthy.
    """

    def __init__(self, host="127.0.0.1", port=50001, timeout=30, retries=3, retry_delay=0.35):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.retry_delay = float(retry_delay)
        self._id = 0
        self._lock = threading.Lock()

    def _next_id(self):
        with self._lock:
            self._id += 1
            return self._id

    def call(self, method, params=None):
        params = params or []
        last_exc = None
        for attempt in range(1, self.retries + 1):
            req_id = self._next_id()
            payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
            try:
                with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
                    sock.settimeout(self.timeout)
                    sock.sendall(payload.encode("utf-8"))
                    buf = b""
                    while b"\n" not in buf:
                        chunk = sock.recv(262144)
                        if not chunk:
                            break
                        buf += chunk
                if not buf:
                    raise ElectrumError("Electrum returned no data")
                try:
                    response = json.loads(buf.split(b"\n", 1)[0])
                except json.JSONDecodeError as exc:
                    raise ElectrumError("Invalid Electrum JSON response") from exc
                if response.get("error"):
                    raise ElectrumError(str(response["error"]))
                return response.get("result")
            except (OSError, ElectrumError) as exc:
                last_exc = exc
                if attempt < self.retries:
                    time.sleep(self.retry_delay * attempt)
                    continue
                break
        raise ElectrumError(f"Electrum connection failed after {self.retries} attempts: {last_exc}") from last_exc

    def version(self):
        return self.call("server.version", ["xpub-watch", "1.4"])

    def history(self, scripthash):
        return self.call("blockchain.scripthash.get_history", [scripthash])

    def listunspent(self, scripthash):
        return self.call("blockchain.scripthash.listunspent", [scripthash])

    def transaction(self, txid, verbose=False):
        return self.call("blockchain.transaction.get", [txid, bool(verbose)])

    def tip(self):
        return self.call("blockchain.headers.subscribe", [])
