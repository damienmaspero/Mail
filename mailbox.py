"""
Shared in-memory mailbox storage used by the SMTP server.

Each message is stored as a dict with the following keys:
    - from_addr  (str)          : envelope sender
    - to_addrs   (list[str])    : envelope recipients
    - data        (str)          : raw message content (headers + body)
    - size        (int)          : byte length of *data*
"""

from __future__ import annotations

import threading
from typing import List, Dict, Any


class Mailbox:
    """Thread-safe in-memory mailbox."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._messages: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def store(self, from_addr: str, to_addrs: List[str], data: str) -> int:
        """Store a new message and return its 1-based message number."""
        message = {
            "from_addr": from_addr,
            "to_addrs": list(to_addrs),
            "data": data,
            "size": len(data.encode()),
            "deleted": False,
        }
        with self._lock:
            self._messages.append(message)
            return len(self._messages)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return the number of non-deleted messages."""
        with self._lock:
            return sum(1 for m in self._messages if not m["deleted"])

    def total_size(self) -> int:
        """Return the total byte size of non-deleted messages."""
        with self._lock:
            return sum(m["size"] for m in self._messages if not m["deleted"])

    def list_messages(self) -> List[Dict[str, Any]]:
        """Return a list of (msg_num, size) for non-deleted messages."""
        with self._lock:
            return [
                {"num": i + 1, "size": m["size"]}
                for i, m in enumerate(self._messages)
                if not m["deleted"]
            ]

    def get(self, msg_num: int) -> Dict[str, Any]:
        """
        Return the message at *msg_num* (1-based).

        Raises IndexError if the message number is out of range or the
        message has been marked as deleted.
        """
        with self._lock:
            if msg_num < 1 or msg_num > len(self._messages):
                raise IndexError(f"No such message: {msg_num}")
            msg = self._messages[msg_num - 1]
            if msg["deleted"]:
                raise IndexError(f"Message {msg_num} has been deleted")
            return dict(msg)

    # ------------------------------------------------------------------
    # Delete / reset
    # ------------------------------------------------------------------

    def mark_deleted(self, msg_num: int) -> None:
        """Mark message *msg_num* (1-based) as deleted."""
        with self._lock:
            if msg_num < 1 or msg_num > len(self._messages):
                raise IndexError(f"No such message: {msg_num}")
            self._messages[msg_num - 1]["deleted"] = True

    def reset(self) -> None:
        """Un-delete all messages (POP3 RSET semantics)."""
        with self._lock:
            for m in self._messages:
                m["deleted"] = False

    def expunge(self) -> None:
        """Permanently remove messages that are marked as deleted."""
        with self._lock:
            self._messages = [m for m in self._messages if not m["deleted"]]

    def clear(self) -> None:
        """Remove *all* messages (used in tests)."""
        with self._lock:
            self._messages.clear()
