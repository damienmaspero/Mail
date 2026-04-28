"""
Integration tests for the SMTP server.

The tests start a real SMTPServer on a random OS-assigned port and talk to it
using plain sockets so no external dependencies are required.
"""

from __future__ import annotations

import socket
import time
import unittest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mailbox import Mailbox
from smtp_server import SMTPServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect(port: int) -> socket.socket:
    """Open a raw TCP connection to the local SMTP server."""
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    return s


def _readline(sock: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        buf += chunk
    return buf.decode().rstrip("\r\n")


def _send(sock: socket.socket, line: str) -> None:
    sock.sendall((line + "\r\n").encode())


def _exchange(sock: socket.socket, cmd: str) -> str:
    _send(sock, cmd)
    return _readline(sock)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSMTPServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.box = Mailbox()
        cls.server = SMTPServer(host="127.0.0.1", port=0, mailbox=cls.box)
        cls.server.start()
        # Grab the OS-assigned port
        cls.port = cls.server._server_sock.getsockname()[1]
        time.sleep(0.05)  # let the accept-loop spin up

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def setUp(self) -> None:
        self.box.clear()
        self.sock = _connect(self.port)
        # Read the server greeting
        greeting = _readline(self.sock)
        self.assertTrue(greeting.startswith("220"), greeting)

    def tearDown(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # HELO / EHLO
    # ------------------------------------------------------------------

    def test_helo(self) -> None:
        resp = _exchange(self.sock, "HELO client.example.com")
        self.assertTrue(resp.startswith("250"), resp)

    def test_ehlo(self) -> None:
        resp = _exchange(self.sock, "EHLO client.example.com")
        self.assertTrue(resp.startswith("250"), resp)

    # ------------------------------------------------------------------
    # MAIL FROM / RCPT TO / DATA
    # ------------------------------------------------------------------

    def _send_mail(
        self,
        from_addr: str = "alice@example.com",
        to_addr: str = "bob@example.com",
        body: str = "Subject: Hi\r\n\r\nHello!",
    ) -> str:
        _exchange(self.sock, "HELO test")
        _exchange(self.sock, f"MAIL FROM:<{from_addr}>")
        _exchange(self.sock, f"RCPT TO:<{to_addr}>")
        _exchange(self.sock, "DATA")
        _send(self.sock, body)
        _send(self.sock, ".")
        return _readline(self.sock)

    def test_full_transaction(self) -> None:
        resp = self._send_mail()
        self.assertTrue(resp.startswith("250"), resp)
        self.assertEqual(self.box.count(), 1)

    def test_message_stored_correctly(self) -> None:
        self._send_mail(
            from_addr="sender@example.com",
            to_addr="recipient@example.com",
            body="Subject: Test\r\n\r\nBody text.",
        )
        msg = self.box.get(1)
        self.assertEqual(msg["from_addr"], "sender@example.com")
        self.assertIn("recipient@example.com", msg["to_addrs"])
        self.assertIn("Body text.", msg["data"])

    def test_multiple_recipients(self) -> None:
        _exchange(self.sock, "HELO test")
        _exchange(self.sock, "MAIL FROM:<sender@example.com>")
        _exchange(self.sock, "RCPT TO:<bob@example.com>")
        _exchange(self.sock, "RCPT TO:<carol@example.com>")
        _exchange(self.sock, "DATA")
        _send(self.sock, "Subject: multi\r\n\r\nHi all.")
        _send(self.sock, ".")
        _readline(self.sock)  # 250

        msg = self.box.get(1)
        self.assertIn("bob@example.com", msg["to_addrs"])
        self.assertIn("carol@example.com", msg["to_addrs"])

    def test_multiple_messages(self) -> None:
        for i in range(3):
            self._send_mail(body=f"Message {i}")
        self.assertEqual(self.box.count(), 3)

    # ------------------------------------------------------------------
    # RSET
    # ------------------------------------------------------------------

    def test_rset_clears_transaction(self) -> None:
        _exchange(self.sock, "HELO test")
        _exchange(self.sock, "MAIL FROM:<a@example.com>")
        resp = _exchange(self.sock, "RSET")
        self.assertTrue(resp.startswith("250"), resp)
        # After RSET, DATA should fail (no RCPT)
        _exchange(self.sock, "MAIL FROM:<a@example.com>")
        resp = _exchange(self.sock, "DATA")
        self.assertTrue(resp.startswith("503"), resp)

    # ------------------------------------------------------------------
    # NOOP
    # ------------------------------------------------------------------

    def test_noop(self) -> None:
        _exchange(self.sock, "HELO test")
        resp = _exchange(self.sock, "NOOP")
        self.assertTrue(resp.startswith("250"), resp)

    # ------------------------------------------------------------------
    # QUIT
    # ------------------------------------------------------------------

    def test_quit(self) -> None:
        resp = _exchange(self.sock, "QUIT")
        self.assertTrue(resp.startswith("221"), resp)

    # ------------------------------------------------------------------
    # Error cases
    # ------------------------------------------------------------------

    def test_mail_before_helo(self) -> None:
        resp = _exchange(self.sock, "MAIL FROM:<a@example.com>")
        self.assertTrue(resp.startswith("503"), resp)

    def test_data_without_rcpt(self) -> None:
        _exchange(self.sock, "HELO test")
        _exchange(self.sock, "MAIL FROM:<a@example.com>")
        resp = _exchange(self.sock, "DATA")
        self.assertTrue(resp.startswith("503"), resp)

    def test_rcpt_without_mail(self) -> None:
        _exchange(self.sock, "HELO test")
        resp = _exchange(self.sock, "RCPT TO:<b@example.com>")
        self.assertTrue(resp.startswith("503"), resp)

    def test_unknown_command(self) -> None:
        _exchange(self.sock, "HELO test")
        resp = _exchange(self.sock, "XYZZY notacommand")
        self.assertTrue(resp.startswith("500"), resp)

    # ------------------------------------------------------------------
    # Dot-stuffing
    # ------------------------------------------------------------------

    def test_dot_stuffing(self) -> None:
        """A line beginning with '..' in the body should be un-stuffed."""
        _exchange(self.sock, "HELO test")
        _exchange(self.sock, "MAIL FROM:<a@example.com>")
        _exchange(self.sock, "RCPT TO:<b@example.com>")
        _exchange(self.sock, "DATA")
        _send(self.sock, "Subject: dots\r\n\r\n")
        _send(self.sock, "..This line starts with a dot")
        _send(self.sock, ".")
        _readline(self.sock)

        msg = self.box.get(1)
        self.assertIn(".This line starts with a dot", msg["data"])


if __name__ == "__main__":
    unittest.main()
