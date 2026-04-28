"""
Minimal POP3 server (RFC 1939) – used internally by tests.

This module is *not* part of the public API; its sole purpose is to provide
a real POP3 endpoint so that the POP3 client tests can exercise every command
against an actual server implementation rather than a mock.

Supported commands
------------------
USER / PASS / STAT / LIST / RETR / DELE / RSET / NOOP / QUIT

Authentication
--------------
Any username is accepted; the password must equal ``"password"`` (or the
value passed to :class:`POP3Server` as *password*).
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Optional

from mailbox import Mailbox

logger = logging.getLogger(__name__)


class _POP3Session:
    """Handle a single client connection."""

    _ST_AUTHORIZATION = "AUTHORIZATION"
    _ST_TRANSACTION = "TRANSACTION"
    _ST_UPDATE = "UPDATE"

    def __init__(
        self,
        conn: socket.socket,
        mailbox: Mailbox,
        password: str,
    ) -> None:
        self._conn = conn
        self._mailbox = mailbox
        self._password = password
        self._state = self._ST_AUTHORIZATION
        self._username: Optional[str] = None
        self._file = conn.makefile("rb")

    # ------------------------------------------------------------------

    def _send(self, line: str) -> None:
        self._conn.sendall((line + "\r\n").encode())
        logger.debug("S: %s", line)

    def _send_multiline(self, header: str, lines: list) -> None:
        self._send(header)
        for line in lines:
            if line.startswith("."):
                line = "." + line
            self._conn.sendall((line + "\r\n").encode())
        self._conn.sendall(b".\r\n")

    def _readline(self) -> Optional[str]:
        line = self._file.readline()
        if not line:
            return None
        return line.decode(errors="replace").rstrip("\r\n")

    # ------------------------------------------------------------------

    def run(self) -> None:
        self._send("+OK POP3 server ready")
        try:
            while True:
                line = self._readline()
                if line is None:
                    break
                logger.debug("C: %s", line)
                self._dispatch(line)
                if self._state == self._ST_UPDATE:
                    break
        finally:
            if self._state == self._ST_UPDATE:
                self._mailbox.expunge()
            self._conn.close()

    def _dispatch(self, line: str) -> None:
        parts = line.split(None, 1)
        cmd = parts[0].upper() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if self._state == self._ST_AUTHORIZATION:
            if cmd == "USER":
                self._username = arg
                self._send(f"+OK {arg} welcome")
            elif cmd == "PASS":
                if arg == self._password:
                    self._state = self._ST_TRANSACTION
                    self._send("+OK mailbox locked and ready")
                else:
                    self._send("-ERR invalid password")
            elif cmd == "QUIT":
                self._send("+OK bye")
                self._state = self._ST_UPDATE
            else:
                self._send("-ERR command not permitted in this state")

        elif self._state == self._ST_TRANSACTION:
            if cmd == "STAT":
                count = self._mailbox.count()
                size = self._mailbox.total_size()
                self._send(f"+OK {count} {size}")
            elif cmd == "LIST":
                if arg:
                    try:
                        info = self._mailbox.list_messages()
                        for item in info:
                            if item["num"] == int(arg):
                                self._send(f"+OK {item['num']} {item['size']}")
                                return
                        self._send("-ERR no such message")
                    except (ValueError, IndexError):
                        self._send("-ERR no such message")
                else:
                    items = self._mailbox.list_messages()
                    self._send_multiline(
                        f"+OK {len(items)} messages",
                        [f"{i['num']} {i['size']}" for i in items],
                    )
            elif cmd == "RETR":
                try:
                    msg = self._mailbox.get(int(arg))
                    lines = msg["data"].splitlines()
                    self._send_multiline(f"+OK {msg['size']} octets", lines)
                except (ValueError, IndexError):
                    self._send("-ERR no such message")
            elif cmd == "DELE":
                try:
                    self._mailbox.mark_deleted(int(arg))
                    self._send(f"+OK message {arg} deleted")
                except (ValueError, IndexError):
                    self._send("-ERR no such message")
            elif cmd == "RSET":
                self._mailbox.reset()
                self._send("+OK")
            elif cmd == "NOOP":
                self._send("+OK")
            elif cmd == "QUIT":
                self._send("+OK bye")
                self._state = self._ST_UPDATE
            else:
                self._send("-ERR command not recognized")


class POP3Server:
    """
    Minimal multi-threaded POP3 server for testing.

    Parameters
    ----------
    host:
        Interface to bind.
    port:
        TCP port (``0`` lets the OS pick a free port).
    mailbox:
        Shared :class:`~mailbox.Mailbox` instance.
    password:
        Password accepted for any username (default ``"password"``).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        mailbox: Optional[Mailbox] = None,
        password: str = "password",
    ) -> None:
        self.host = host
        self.port = port
        self.mailbox = mailbox or Mailbox()
        self.password = password
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(10)
        self.port = self._server_sock.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except OSError:
                break
            t = threading.Thread(
                target=self._handle, args=(conn,), daemon=True
            )
            t.start()

    def _handle(self, conn: socket.socket) -> None:
        session = _POP3Session(conn, self.mailbox, self.password)
        try:
            session.run()
        except Exception:
            logger.exception("Unhandled error in POP3 session")
        finally:
            try:
                conn.close()
            except OSError:
                pass
