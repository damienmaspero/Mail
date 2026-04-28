"""
Integration tests for the POP3 client.

The tests start a real POP3Server on a random OS-assigned port so no external
server is required.
"""

from __future__ import annotations

import time
import unittest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from mailbox import Mailbox
from pop3_server import POP3Server
from pop_client import POP3Client, POP3Error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASSWORD = "secret"


def _seed_mailbox(box: Mailbox, count: int = 3) -> None:
    for i in range(1, count + 1):
        box.store(
            f"sender{i}@example.com",
            [f"recipient{i}@example.com"],
            f"Subject: Message {i}\r\n\r\nBody of message {i}.",
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPOP3Client(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.box = Mailbox()
        cls.server = POP3Server(
            host="127.0.0.1", port=0, mailbox=cls.box, password=PASSWORD
        )
        cls.server.start()
        time.sleep(0.05)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()

    def setUp(self) -> None:
        self.box.clear()
        _seed_mailbox(self.box, 3)
        self.client = POP3Client("127.0.0.1", self.server.port, timeout=5)
        self.client.connect()
        self.client.user("testuser")
        self.client.pass_(PASSWORD)

    def tearDown(self) -> None:
        try:
            self.client.quit()
        except Exception:
            pass
        self.client.close()

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def test_wrong_password_raises(self) -> None:
        client = POP3Client("127.0.0.1", self.server.port, timeout=5)
        client.connect()
        client.user("alice")
        with self.assertRaises(POP3Error):
            client.pass_("wrong")
        client.close()

    # ------------------------------------------------------------------
    # STAT
    # ------------------------------------------------------------------

    def test_stat(self) -> None:
        count, total = self.client.stat()
        self.assertEqual(count, 3)
        self.assertGreater(total, 0)

    def test_stat_empty_mailbox(self) -> None:
        self.box.clear()
        count, total = self.client.stat()
        self.assertEqual(count, 0)
        self.assertEqual(total, 0)

    # ------------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------------

    def test_list_all(self) -> None:
        items = self.client.list()
        self.assertEqual(len(items), 3)
        for num, size in items:
            self.assertGreater(size, 0)

    def test_list_single(self) -> None:
        items = self.client.list(msg_num=1)
        self.assertEqual(len(items), 1)
        num, size = items[0]
        self.assertEqual(num, 1)
        self.assertGreater(size, 0)

    # ------------------------------------------------------------------
    # RETR
    # ------------------------------------------------------------------

    def test_retr(self) -> None:
        message = self.client.retr(1)
        self.assertIn("Subject: Message 1", message)
        self.assertIn("Body of message 1", message)

    def test_retr_all_messages(self) -> None:
        for i in range(1, 4):
            msg = self.client.retr(i)
            self.assertIn(f"Message {i}", msg)

    # ------------------------------------------------------------------
    # DELE / RSET
    # ------------------------------------------------------------------

    def test_dele(self) -> None:
        resp = self.client.dele(1)
        self.assertIn("+OK", resp)
        # After QUIT the server expunges; check via a new connection
        self.client.quit()
        self.client.close()

        # Reconnect and verify count
        client2 = POP3Client("127.0.0.1", self.server.port, timeout=5)
        client2.connect()
        client2.user("u")
        client2.pass_(PASSWORD)
        count, _ = client2.stat()
        client2.quit()
        client2.close()
        self.assertEqual(count, 2)

    def test_rset_undeletes(self) -> None:
        self.client.dele(1)
        resp = self.client.rset()
        self.assertIn("+OK", resp)
        # Count should still be 3 (deletion rolled back)
        count, _ = self.client.stat()
        self.assertEqual(count, 3)

    # ------------------------------------------------------------------
    # NOOP
    # ------------------------------------------------------------------

    def test_noop(self) -> None:
        resp = self.client.noop()
        self.assertIn("+OK", resp)

    # ------------------------------------------------------------------
    # QUIT
    # ------------------------------------------------------------------

    def test_quit(self) -> None:
        resp = self.client.quit()
        self.assertIn("+OK", resp)
        # Prevent tearDown from sending QUIT again
        self.client.close()
        self.client = POP3Client("127.0.0.1", self.server.port, timeout=5)
        self.client.connect()
        self.client.user("u")
        self.client.pass_(PASSWORD)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def test_context_manager(self) -> None:
        # Ensure a fresh connection is used
        self.client.quit()
        self.client.close()

        with POP3Client("127.0.0.1", self.server.port, timeout=5) as c:
            c.user("u")
            c.pass_(PASSWORD)
            count, _ = c.stat()
            self.assertEqual(count, 3)

        # Re-create client so tearDown doesn't fail
        self.client = POP3Client("127.0.0.1", self.server.port, timeout=5)
        self.client.connect()
        self.client.user("u")
        self.client.pass_(PASSWORD)


if __name__ == "__main__":
    unittest.main()
