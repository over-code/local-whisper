"""Tiny request/response IPC over a Unix socket.

The daemon owns the socket; ``local-whisper toggle`` (which is what the KDE
global shortcut actually runs) is a 10 ms client that writes one JSON line and
reads one back. Keeping this dependency-free means the CLI stays instant even
though the daemon has a GUI and a speech model attached to it.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import paths
from .logging_setup import get

log = get("ipc")

Handler = Callable[[str, dict], dict]

_TIMEOUT = 5.0


class ServerAlreadyRunning(RuntimeError):
    pass


def _connect(path: Path, timeout: float = _TIMEOUT) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    client.connect(str(path))
    return client


def send(command: str, payload: dict | None = None, *, timeout: float = _TIMEOUT) -> dict:
    """Send one command to a running daemon and return its reply.

    Raises ``ConnectionError`` when no daemon is listening.
    """
    path = paths.socket_path()
    if not path.exists():
        raise ConnectionError("local-whisper daemon is not running")
    try:
        with _connect(path, timeout) as client:
            message = json.dumps({"command": command, "payload": payload or {}}) + "\n"
            client.sendall(message.encode("utf-8"))
            client.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
    except (ConnectionRefusedError, FileNotFoundError) as exc:
        # A socket file left behind by a crashed daemon.
        raise ConnectionError("local-whisper daemon is not running") from exc
    except OSError as exc:
        raise ConnectionError(f"cannot talk to the daemon: {exc}") from exc

    raw = b"".join(chunks).decode("utf-8", "replace").strip()
    if not raw:
        return {"ok": True}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"malformed reply: {raw[:200]}"}


def is_running() -> bool:
    try:
        reply = send("ping", timeout=1.5)
    except ConnectionError:
        return False
    return bool(reply.get("ok"))


class Server:
    """Accept loop on a background thread; handlers run on that thread.

    The GUI hands in a handler that only marshals the command onto the Qt
    thread, so nothing here needs to know about Qt.
    """

    def __init__(self, handler: Handler, path: Path | None = None) -> None:
        self.path = path or paths.socket_path()
        self._handler = handler
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            if is_running_at(self.path):
                raise ServerAlreadyRunning(f"another daemon owns {self.path}")
            log.info("removing stale socket %s", self.path)
            self.path.unlink(missing_ok=True)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._serve, name="lw-ipc", daemon=True)
        self._thread.start()
        log.info("listening on %s", self.path)

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(_TIMEOUT)
                    self._handle(conn)
                except Exception:  # one bad client must not kill the loop
                    log.exception("error while handling an IPC request")

    def _handle(self, conn: socket.socket) -> None:
        buffer = b""
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buffer += chunk
            if len(buffer) > 1_000_000:
                break
        line = buffer.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
        if not line:
            return
        try:
            request = json.loads(line)
            command = str(request.get("command", ""))
            payload = request.get("payload") or {}
        except json.JSONDecodeError:
            conn.sendall(b'{"ok": false, "error": "invalid JSON"}\n')
            return

        try:
            reply: dict[str, Any] = self._handler(command, payload) or {"ok": True}
        except Exception as exc:
            log.exception("handler failed for %r", command)
            reply = {"ok": False, "error": str(exc)}
        conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self.path.unlink(missing_ok=True)


def is_running_at(path: Path) -> bool:
    try:
        with _connect(path, 1.0) as client:
            client.sendall(b'{"command": "ping"}\n')
            client.shutdown(socket.SHUT_WR)
            return bool(client.recv(4096))
    except OSError:
        return False
