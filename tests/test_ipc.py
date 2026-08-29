import json
import socket

import pytest

from localwhisper import ipc, paths


@pytest.fixture
def server():
    received = []

    def handler(command, payload):
        received.append((command, payload))
        if command == "boom":
            raise RuntimeError("handler exploded")
        return {"ok": True, "echo": command}

    server = ipc.Server(handler)
    server.start()
    server.received = received
    yield server
    server.stop()


def test_send_and_receive(server):
    assert ipc.send("toggle", {"a": 1}) == {"ok": True, "echo": "toggle"}
    assert server.received[-1] == ("toggle", {"a": 1})


def test_is_running(server):
    assert ipc.is_running() is True


def test_no_daemon_raises_connection_error():
    with pytest.raises(ConnectionError):
        ipc.send("toggle")


def test_handler_errors_become_replies(server):
    reply = ipc.send("boom")
    assert reply["ok"] is False and "exploded" in reply["error"]


def test_invalid_json_is_rejected(server):
    client = socket.socket(socket.AF_UNIX)
    client.connect(str(paths.socket_path()))
    client.sendall(b"this is not json\n")
    client.shutdown(socket.SHUT_WR)
    assert json.loads(client.recv(4096))["ok"] is False
    client.close()


def test_stale_socket_is_replaced(tmp_path):
    path = paths.socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")  # a leftover file from a crashed daemon
    server = ipc.Server(lambda c, p: {"ok": True})
    server.start()
    try:
        assert ipc.is_running()
    finally:
        server.stop()
