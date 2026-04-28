# Mail

Mail basics – a minimal Python implementation of an **SMTP server** and a
**POP3 client** (plus a lightweight POP3 server used by tests).

---

## Repository layout

```
smtp_server.py   – SMTP server (RFC 5321)
pop_client.py    – POP3 client (RFC 1939)
pop3_server.py   – Minimal POP3 server (used by tests)
mailbox.py       – Thread-safe in-memory mailbox shared by server components
tests/
  test_smtp_server.py  – Integration tests for the SMTP server
  test_pop_client.py   – Integration tests for the POP3 client
```

---

## SMTP Server

Receives incoming e-mail and stores it in an in-memory
[`Mailbox`](mailbox.py).

### Supported commands

| Command | Description |
|---------|-------------|
| `HELO` / `EHLO` | Open a mail session |
| `MAIL FROM:<addr>` | Set the envelope sender |
| `RCPT TO:<addr>` | Add an envelope recipient (repeatable) |
| `DATA` | Transfer the message body |
| `RSET` | Reset the current mail transaction |
| `NOOP` | No-op (keep-alive) |
| `QUIT` | End the session |

### Run standalone

```bash
python smtp_server.py --host 127.0.0.1 --port 2525
```

### Embed in your own code

```python
from mailbox import Mailbox
from smtp_server import SMTPServer

box = Mailbox()
server = SMTPServer(host="127.0.0.1", port=2525, mailbox=box)
server.start()          # runs in a background daemon thread

# … later …
server.stop()
```

---

## POP3 Client

Connects to any POP3 server and retrieves mail.

### Supported commands

| Method | POP3 command |
|--------|--------------|
| `user(username)` | `USER` |
| `pass_(password)` | `PASS` |
| `stat()` | `STAT` – returns `(count, total_bytes)` |
| `list(msg_num=None)` | `LIST` – returns `[(num, size), …]` |
| `retr(msg_num)` | `RETR` – returns the raw message string |
| `dele(msg_num)` | `DELE` |
| `rset()` | `RSET` |
| `noop()` | `NOOP` |
| `quit()` | `QUIT` |

### Use as a context manager

```python
from pop_client import POP3Client

with POP3Client("mail.example.com", port=110) as client:
    client.user("alice")
    client.pass_("secret")

    count, total = client.stat()
    print(f"{count} messages, {total} bytes")

    for num, size in client.list():
        print(f"  #{num}: {size} bytes")
        print(client.retr(num))
```

### Run standalone

```bash
python pop_client.py mail.example.com --user alice --password secret
# Retrieve message 1:
python pop_client.py mail.example.com --user alice --password secret --retr 1
```

---

## Running the tests

```bash
python -m unittest discover -s tests -v
```
