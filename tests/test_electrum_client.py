"""Real ElectrumClient against a local protocol server."""
import pytest

from electrum_server import serve
from helpers import ZPUB, kinds, spk


@pytest.fixture
def server(chain):
    srv = serve(chain)
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def client(server):
    from electrum_client import ElectrumClient

    c = ElectrumClient("127.0.0.1", server.server_address[1], timeout=5, retry_delay=0.01, batch_size=7)
    yield c
    c.close()


def test_pipelined_requests_reuse_one_connection(server, client, chain):
    from wallet import electrum_scripthash

    shs = [electrum_scripthash(spk(ZPUB, i)) for i in range(25)]
    chain.pay(spk(ZPUB, 3), 1_000)
    statuses = client.subscribe_many(shs)
    assert statuses[3] is not None and statuses.count(None) == 24
    hist = client.history_many(shs)
    assert len(hist[3]) == 1 and hist[0] == []
    assert client.tip()["height"] == chain.tip_height
    assert server.connections == 1
    # server answers each burst in reverse order with a push notification in between
    assert hist == [chain._history(sh) for sh in shs]


def test_reconnects_after_connection_drop(server, client, chain):
    client.tip()
    server.drop_after = 0
    assert client.tip()["height"] == chain.tip_height
    assert server.connections == 2


def test_server_error_is_not_retried(server, client):
    from electrum_client import ElectrumError

    with pytest.raises(ElectrumError):
        client.call("no.such.method", [])
    assert server.requests.count("no.such.method") == 1
    assert client.tip()  # connection still in sync after the error


def test_connection_refused_raises():
    import socket

    from electrum_client import ElectrumConnectionError, ElectrumClient

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = ElectrumClient("127.0.0.1", port, timeout=1, retries=2, retry_delay=0.01)
    with pytest.raises(ElectrumConnectionError):
        c.tip()


def test_watcher_end_to_end_over_tcp(db, chain, server, sent):
    import watcher as w
    from electrum_client import ElectrumClient

    client = ElectrumClient("127.0.0.1", server.server_address[1], timeout=5)
    watcher = w.Watcher(client=client)
    db.add_wallet("Main", ZPUB, "main", "p2wpkh")
    watcher.scan_once()
    op = chain.pay(spk(ZPUB, 0), 12_345, height=chain.tip_height)
    watcher.scan_once()
    chain.spend([op])
    watcher.scan_once()
    assert kinds(sent) == ["receive", "spend"]

    # Unchanged wallet: only status checks, no history or transaction fetches.
    before = len(server.requests)
    watcher.scan_once()
    new = server.requests[before:]
    assert "blockchain.scripthash.get_history" not in new
    assert "blockchain.transaction.get" not in new
    assert server.connections == 1
    client.close()
