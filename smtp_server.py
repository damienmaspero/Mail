"""
Minimal SMTP server (RFC 5321).

Supported commands
------------------
HELO <domain>
EHLO <domain>
MAIL FROM:<address>
RCPT TO:<address>
DATA
RSET
NOOP
QUIT

Usage
-----
Run standalone::

    python smtp_server.py [--host HOST] [--port PORT]

Or embed in another program::

    from mailbox import Mailbox
    from smtp_server import SMTPServer

    box = Mailbox()
    server = SMTPServer(host="127.0.0.1", port=2525, mailbox=box)
    server.start()   # non-blocking; runs in a background thread
    ...
    server.stop()
"""

from __future__ import annotations

import argparse
import logging
import re
import socket
import threading
from typing import List, Optional

from mailbox import Mailbox

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADDR_RE = re.compile(r"<([^>]*)>")


def _extract_address(value: str) -> Optional[str]:
    """Return the address inside angle-brackets, or *None* if not found."""
    m = _ADDR_RE.search(value)
    if m:
        return m.group(1).strip()
    # Some clients omit brackets: accept the raw token after the colon
    parts = value.split(":", 1)
    if len(parts) == 2:
        return parts[1].strip()
    return None


# ---------------------------------------------------------------------------
# Per-connection session
# ---------------------------------------------------------------------------

class _SMTPSession:
    """Handle a single client connection."""

    # FSM states
    _ST_CONNECTED = "CONNECTED"
    _ST_GREETED = "GREETED"
    _ST_MAIL = "MAIL"
    _ST_RCPT = "RCPT"
    _ST_DATA = "DATA"
    _ST_QUIT = "QUIT"

    def __init__(self, conn: socket.socket, addr: tuple, mailbox: Mailbox) -> None:
        self._conn = conn
        self._addr = addr
        self._mailbox = mailbox
        self._state = self._ST_CONNECTED
        self._from_addr: Optional[str] = None
        self._to_addrs: List[str] = []
        self._data_lines: List[str] = []

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    def _send(self, line: str) -> None:
        self._conn.sendall((line + "\r\n").encode())
        logger.debug("S: %s", line)

    def _readline(self) -> Optional[str]:
        buf = b""
        while True:
            try:
                ch = self._conn.recv(1)
            except OSError:
                return None
            if not ch:
                return None
            buf += ch
            if buf.endswith(b"\n"):
                return buf.decode(errors="replace").rstrip("\r\n")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._send("220 localhost SMTP Service Ready")
        try:
            while True:
                if self._state == self._ST_DATA:
                    self._read_data()
                else:
                    line = self._readline()
                    if line is None:
                        break
                    logger.debug("C: %s", line)
                    if self._state == self._ST_QUIT:
                        break
                    self._dispatch(line)
                    if self._state == self._ST_QUIT:
                        break
        finally:
            self._conn.close()

    def _dispatch(self, line: str) -> None:
        upper = line.upper()
        if upper.startswith("HELO") or upper.startswith("EHLO"):
            self._cmd_helo(line)
        elif upper.startswith("MAIL FROM"):
            self._cmd_mail(line)
        elif upper.startswith("RCPT TO"):
            self._cmd_rcpt(line)
        elif upper.strip() == "DATA":
            self._cmd_data_start()
        elif upper.strip() == "RSET":
            self._cmd_rset()
        elif upper.strip() == "NOOP":
            self._send("250 OK")
        elif upper.strip() == "QUIT":
            self._send("221 Bye")
            self._state = self._ST_QUIT
        else:
            self._send("500 Command not recognized")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_helo(self, line: str) -> None:
        parts = line.split(None, 1)
        domain = parts[1] if len(parts) > 1 else "unknown"
        self._reset_transaction()
        self._state = self._ST_GREETED
        self._send(f"250 Hello {domain}")

    def _cmd_mail(self, line: str) -> None:
        if self._state not in (self._ST_GREETED,):
            if self._state == self._ST_CONNECTED:
                self._send("503 Send HELO/EHLO first")
                return
        addr = _extract_address(line)
        if addr is None:
            self._send("501 Syntax: MAIL FROM:<address>")
            return
        self._reset_transaction()
        self._from_addr = addr
        self._state = self._ST_MAIL
        self._send("250 OK")

    def _cmd_rcpt(self, line: str) -> None:
        if self._state not in (self._ST_MAIL, self._ST_RCPT):
            self._send("503 Need MAIL FROM before RCPT TO")
            return
        addr = _extract_address(line)
        if addr is None:
            self._send("501 Syntax: RCPT TO:<address>")
            return
        self._to_addrs.append(addr)
        self._state = self._ST_RCPT
        self._send("250 OK")

    def _cmd_data_start(self) -> None:
        if self._state != self._ST_RCPT:
            self._send("503 Need RCPT TO before DATA")
            return
        self._state = self._ST_DATA
        self._send("354 Start mail input; end with <CRLF>.<CRLF>")

    def _read_data(self) -> None:
        """Read the message body until a lone '.' line."""
        lines: List[str] = []
        while True:
            line = self._readline()
            if line is None:
                # Connection dropped mid-data; discard
                self._reset_transaction()
                self._state = self._ST_GREETED
                return
            if line == ".":
                break
            # Un-dot-stuff: leading double dot → single dot
            if line.startswith(".."):
                line = line[1:]
            lines.append(line)
        data = "\r\n".join(lines)
        self._mailbox.store(self._from_addr or "", list(self._to_addrs), data)
        self._reset_transaction()
        self._state = self._ST_GREETED
        self._send("250 OK: message queued")

    def _cmd_rset(self) -> None:
        self._reset_transaction()
        if self._state not in (self._ST_CONNECTED,):
            self._state = self._ST_GREETED
        self._send("250 OK")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reset_transaction(self) -> None:
        self._from_addr = None
        self._to_addrs = []
        self._data_lines = []


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class SMTPServer:
    """
    Multi-threaded SMTP server.

    Parameters
    ----------
    host:
        Interface to bind (default ``"127.0.0.1"``).
    port:
        TCP port to listen on (default ``2525``).
    mailbox:
        :class:`~mailbox.Mailbox` instance where incoming messages are stored.
        A new empty mailbox is created when not provided.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 2525,
        mailbox: Optional[Mailbox] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.mailbox = mailbox or Mailbox()
        self._server_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the server in a background daemon thread."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(10)
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        logger.info("SMTP server listening on %s:%d", self.host, self.port)

    def stop(self) -> None:
        """Stop the server and close the listening socket."""
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Accept loop
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except OSError:
                break
            t = threading.Thread(
                target=self._handle,
                args=(conn, addr),
                daemon=True,
            )
            t.start()

    def _handle(self, conn: socket.socket, addr: tuple) -> None:
        logger.info("Connection from %s:%d", *addr)
        session = _SMTPSession(conn, addr, self.mailbox)
        try:
            session.run()
        except Exception:
            logger.exception("Unhandled error in SMTP session")
        finally:
            try:
                conn.close()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(description="Simple SMTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2525)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    box = Mailbox()
    server = SMTPServer(host=args.host, port=args.port, mailbox=box)
    server.start()
    print(f"SMTP server running on {args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        while True:
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    server.stop()


if __name__ == "__main__":
    _main()
