"""
POP3 client (RFC 1939).

Supported commands
------------------
USER <username>
PASS <password>
STAT
LIST [msg]
RETR <msg>
DELE <msg>
RSET
NOOP
QUIT

Usage
-----
Connect manually::

    from pop_client import POP3Client

    client = POP3Client(host="mail.example.com", port=110)
    client.connect()
    client.user("alice")
    client.pass_("secret")

    count, total = client.stat()
    print(f"{count} messages, {total} bytes")

    for info in client.list():
        msg_num, size = info
        message = client.retr(msg_num)
        print(message)

    client.quit()

Or use the context-manager form::

    with POP3Client("mail.example.com") as client:
        client.user("alice")
        client.pass_("secret")
        for num, size in client.list():
            print(client.retr(num))
"""

from __future__ import annotations

import argparse
import logging
import socket
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class POP3Error(Exception):
    """Raised when the server returns an -ERR response."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class POP3Client:
    """
    Minimal POP3 client.

    Parameters
    ----------
    host:
        Hostname or IP address of the POP3 server.
    port:
        TCP port (default ``110``).
    timeout:
        Socket timeout in seconds (default ``10``).
    """

    def __init__(
        self,
        host: str,
        port: int = 110,
        timeout: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._file: Optional[socket.SocketIO] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "POP3Client":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        try:
            self.quit()
        except Exception:
            pass
        self.close()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect(self) -> str:
        """Open the connection and return the server greeting."""
        self._sock = socket.create_connection(
            (self.host, self.port), timeout=self.timeout
        )
        self._file = self._sock.makefile("rb")
        greeting = self._read_line()
        logger.debug("S: %s", greeting)
        if not greeting.startswith("+OK"):
            raise POP3Error(f"Unexpected greeting: {greeting}")
        return greeting

    def close(self) -> None:
        """Close the underlying socket without sending QUIT."""
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ------------------------------------------------------------------
    # Low-level I/O
    # ------------------------------------------------------------------

    def _send(self, line: str) -> None:
        data = (line + "\r\n").encode()
        logger.debug("C: %s", line)
        self._sock.sendall(data)

    def _read_line(self) -> str:
        line = self._file.readline()
        if not line:
            raise POP3Error("Connection closed by server")
        return line.decode(errors="replace").rstrip("\r\n")

    def _expect_ok(self, cmd: str) -> str:
        """Send *cmd*, read one-line response, raise on -ERR."""
        self._send(cmd)
        resp = self._read_line()
        logger.debug("S: %s", resp)
        if not resp.startswith("+OK"):
            raise POP3Error(resp)
        return resp

    def _read_multiline(self) -> List[str]:
        """
        Read a POP3 multi-line response body.

        Lines are returned without the trailing CRLF.  The terminating
        ``"."`` line is consumed but not included.
        """
        lines: List[str] = []
        while True:
            line = self._read_line()
            if line == ".":
                break
            # Byte-stuffing: leading dot on non-terminating lines is doubled
            if line.startswith(".."):
                line = line[1:]
            lines.append(line)
        return lines

    # ------------------------------------------------------------------
    # POP3 commands
    # ------------------------------------------------------------------

    def user(self, username: str) -> str:
        """Send ``USER <username>`` and return the +OK response."""
        return self._expect_ok(f"USER {username}")

    def pass_(self, password: str) -> str:
        """Send ``PASS <password>`` and return the +OK response."""
        return self._expect_ok(f"PASS {password}")

    def stat(self) -> Tuple[int, int]:
        """
        Send ``STAT`` and return ``(message_count, total_octets)``.
        """
        resp = self._expect_ok("STAT")
        # +OK <count> <size>
        parts = resp.split()
        return int(parts[1]), int(parts[2])

    def list(self, msg_num: Optional[int] = None) -> List[Tuple[int, int]]:
        """
        Send ``LIST [msg]`` and return a list of ``(msg_num, size)`` tuples.

        When *msg_num* is provided only a single-line response is read.
        """
        if msg_num is not None:
            resp = self._expect_ok(f"LIST {msg_num}")
            parts = resp.split()
            return [(int(parts[1]), int(parts[2]))]
        resp = self._expect_ok("LIST")
        result: List[Tuple[int, int]] = []
        for line in self._read_multiline():
            parts = line.split()
            if len(parts) >= 2:
                result.append((int(parts[0]), int(parts[1])))
        return result

    def retr(self, msg_num: int) -> str:
        """
        Send ``RETR <msg>`` and return the full message as a string.
        """
        self._expect_ok(f"RETR {msg_num}")
        lines = self._read_multiline()
        return "\r\n".join(lines)

    def dele(self, msg_num: int) -> str:
        """Send ``DELE <msg>`` and return the +OK response."""
        return self._expect_ok(f"DELE {msg_num}")

    def rset(self) -> str:
        """Send ``RSET`` (un-delete all messages) and return the +OK response."""
        return self._expect_ok("RSET")

    def noop(self) -> str:
        """Send ``NOOP`` and return the +OK response."""
        return self._expect_ok("NOOP")

    def quit(self) -> str:
        """Send ``QUIT``, enter UPDATE state, and return the +OK response."""
        return self._expect_ok("QUIT")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(description="Simple POP3 client")
    parser.add_argument("host", help="POP3 server hostname or IP")
    parser.add_argument("--port", type=int, default=110)
    parser.add_argument("--user", required=True, help="Username")
    parser.add_argument("--password", required=True, help="Password")
    parser.add_argument(
        "--retr",
        type=int,
        metavar="MSG",
        help="Retrieve and print message number MSG",
    )
    parser.add_argument(
        "--dele",
        type=int,
        metavar="MSG",
        help="Mark message number MSG for deletion",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with POP3Client(args.host, args.port) as client:
        client.user(args.user)
        client.pass_(args.password)

        count, total = client.stat()
        print(f"Mailbox: {count} message(s), {total} bytes")

        for num, size in client.list():
            print(f"  #{num}: {size} bytes")

        if args.retr:
            print(f"\n--- Message {args.retr} ---")
            print(client.retr(args.retr))

        if args.dele:
            client.dele(args.dele)
            print(f"Message {args.dele} marked for deletion")


if __name__ == "__main__":
    _main()
