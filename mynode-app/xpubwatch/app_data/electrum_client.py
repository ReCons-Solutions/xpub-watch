import json
import logging
import socket
import threading
import time

log = logging.getLogger("xpubwatch.electrum")

CLIENT_NAME = "xpub-watch"
PROTOCOL_VERSION = "1.4"


class ElectrumError(RuntimeError):
    """Error reported by the server for a request (not retried)."""


class ElectrumConnectionError(ElectrumError):
    """Transport problem; the request is retried on a new connection."""


class ElectrumClient(object):
    """Electrum JSON-RPC client for a trusted local electrs.

    Keeps one persistent TCP connection and pipelines requests: a batch of
    N requests costs one round trip instead of N connections. Server push
    notifications (from scripthash subscriptions) are read and discarded;
    the watcher compares status hashes on every poll instead, which stays
    correct across reconnects.
    """

    def __init__(self, host="127.0.0.1", port=50001, timeout=30, retries=3, retry_delay=0.5, batch_size=100):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.retry_delay = float(retry_delay)
        self.batch_size = max(1, int(batch_size))
        self._sock = None
        self._buf = b""
        self._id = 0
        self._lock = threading.RLock()
        self.server_version = None

    # --------------------------------------------------------- transport
    def close(self):
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None
            self._buf = b""

    def _connect(self):
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        self._sock = sock
        self._buf = b""
        # The protocol requires server.version as the first message.
        self.server_version = self._exchange([("server.version", [CLIENT_NAME, PROTOCOL_VERSION])])[0]

    def _next_id(self):
        self._id += 1
        return self._id

    def _read_message(self):
        while b"\n" not in self._buf:
            chunk = self._sock.recv(262144)
            if not chunk:
                raise ElectrumConnectionError("connection closed by server")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        if not line.strip():
            return None
        try:
            return json.loads(line)
        except ValueError as exc:
            raise ElectrumConnectionError("invalid JSON from server") from exc

    def _exchange(self, calls):
        """Send calls pipelined on the open socket and return results in order."""
        ids = []
        payload = []
        for method, params in calls:
            req_id = self._next_id()
            ids.append(req_id)
            payload.append(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))
        self._sock.sendall(("\n".join(payload) + "\n").encode("utf-8"))

        pending = set(ids)
        responses = {}
        while pending:
            msg = self._read_message()
            if msg is None:
                continue
            if isinstance(msg, list):  # batch response from servers that coalesce
                messages = msg
            else:
                messages = [msg]
            for m in messages:
                if not isinstance(m, dict):
                    continue
                rid = m.get("id")
                if rid in pending:
                    pending.discard(rid)
                    responses[rid] = m
                # else: subscription notification or stale response - ignore
        results = []
        for rid, (method, _) in zip(ids, calls):
            m = responses[rid]
            if m.get("error"):
                err = m["error"]
                text = err.get("message") if isinstance(err, dict) else str(err)
                raise ElectrumError("%s failed: %s" % (method, text))
            results.append(m.get("result"))
        return results

    def request_many(self, calls):
        """Run [(method, params), ...]; reconnects and retries on transport errors."""
        if not calls:
            return []
        results = []
        for start in range(0, len(calls), self.batch_size):
            results.extend(self._request_batch(calls[start:start + self.batch_size]))
        return results

    def _request_batch(self, calls):
        last_exc = None
        with self._lock:
            for attempt in range(1, self.retries + 1):
                try:
                    if self._sock is None:
                        self._connect()
                    return self._exchange(calls)
                except ElectrumConnectionError as exc:
                    last_exc = exc
                except (OSError, socket.timeout) as exc:
                    last_exc = exc
                self.close()
                if attempt < self.retries:
                    time.sleep(self.retry_delay * attempt)
        raise ElectrumConnectionError(
            "Electrum connection to %s:%s failed after %d attempts: %s" % (self.host, self.port, self.retries, last_exc)
        )

    def call(self, method, params=None):
        return self.request_many([(method, list(params or []))])[0]

    # ------------------------------------------------------------- API
    def version(self):
        with self._lock:
            if self._sock is None:
                self.request_many([("server.ping", [])])
            return self.server_version

    def tip(self):
        return self.call("blockchain.headers.subscribe", [])

    def history(self, scripthash):
        return self.call("blockchain.scripthash.get_history", [scripthash])

    def listunspent(self, scripthash):
        return self.call("blockchain.scripthash.listunspent", [scripthash])

    def transaction(self, txid, verbose=False):
        return self.call("blockchain.transaction.get", [txid, bool(verbose)])

    def subscribe_many(self, scripthashes):
        return self.request_many([("blockchain.scripthash.subscribe", [sh]) for sh in scripthashes])

    def history_many(self, scripthashes):
        return self.request_many([("blockchain.scripthash.get_history", [sh]) for sh in scripthashes])

    def transactions_raw(self, txids):
        return self.request_many([("blockchain.transaction.get", [t, False]) for t in txids])
