"""Minimal Electrum protocol TCP server backed by FakeElectrum (tests only)."""
import json
import socketserver
import threading


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server
        srv.connections += 1
        buf = b""
        while True:
            chunk = self.request.recv(65536)
            if not chunk:
                return
            buf += chunk
            *lines, buf = buf.split(b"\n")
            backlog = []
            for raw in lines:
                if not raw.strip():
                    continue
                req = json.loads(raw)
                srv.requests.append(req["method"])
                if srv.drop_after is not None:
                    srv.drop_after -= 1
                    if srv.drop_after < 0:
                        srv.drop_after = None
                        return  # close the connection mid-session
                resp = {"jsonrpc": "2.0", "id": req["id"]}
                try:
                    resp["result"] = self.dispatch(req["method"], req["params"])
                except KeyError as exc:
                    resp["error"] = {"code": -32600, "message": "unknown %s" % exc}
                backlog.append(resp)
            # Answer each received burst out of order, preceded by a push notification.
            self.flush(backlog)

    def flush(self, backlog):
        if not backlog:
            return
        out = []
        if self.server.push_notifications:
            out.append({"jsonrpc": "2.0", "method": "blockchain.scripthash.subscribe", "params": ["00" * 32, "ff"]})
        out.extend(reversed(backlog) if self.server.reverse else backlog)
        self.request.sendall(("\n".join(json.dumps(m) for m in out) + "\n").encode())

    def dispatch(self, method, params):
        fake = self.server.fake
        if method == "server.version":
            return ["fake-electrs", "1.4"]
        if method == "server.ping":
            return None
        if method == "blockchain.headers.subscribe":
            return {"height": fake.tip_height, "hex": "00"}
        if method == "blockchain.scripthash.subscribe":
            return fake.status(params[0])
        if method == "blockchain.scripthash.get_history":
            return fake._history(params[0])
        if method == "blockchain.scripthash.listunspent":
            return fake.listunspent(params[0])
        if method == "blockchain.transaction.get":
            return fake.txs[params[0]][0].to_string()
        raise KeyError(method)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(fake):
    server = _Server(("127.0.0.1", 0), _Handler)
    server.fake = fake
    server.requests = []
    server.connections = 0
    server.drop_after = None
    server.reverse = True
    server.push_notifications = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
