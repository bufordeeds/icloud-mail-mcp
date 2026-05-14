"""iCloud Mail MCP Server — IMAP + SMTP access via FastMCP."""

import os
import smtplib
import sys
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime
from html.parser import HTMLParser

from dotenv import load_dotenv
from imapclient import IMAPClient
from mcp.server.fastmcp import FastMCP

load_dotenv()

ICLOUD_EMAIL = os.getenv("ICLOUD_EMAIL", "")
ICLOUD_IMAP_USER = os.getenv("ICLOUD_IMAP_USER", ICLOUD_EMAIL)
ICLOUD_APP_PASSWORD = os.getenv("ICLOUD_APP_PASSWORD", "")
IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587

# ---------------------------------------------------------------------------
# Startup validation — fail loudly rather than silently
# ---------------------------------------------------------------------------

_missing = [v for v in ("ICLOUD_EMAIL", "ICLOUD_APP_PASSWORD") if not os.getenv(v)]
if _missing:
    print(
        f"ERROR: Required environment variable(s) not set: {', '.join(_missing)}\n"
        "Set ICLOUD_EMAIL and ICLOUD_APP_PASSWORD (use an app-specific password "
        "generated at appleid.apple.com) before starting the server.",
        file=sys.stderr,
    )
    sys.exit(1)

mcp = FastMCP("icloud-mail")


# ---------------------------------------------------------------------------
# HTML stripping — stdlib html.parser, handles malformed markup gracefully
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Accumulate visible text, skipping script/style blocks."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag.lower() in ("script", "style"):
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style"):
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        return " ".join("".join(self._parts).split())


def _strip_html(html: str) -> str:
    stripper = _HTMLStripper()
    try:
        stripper.feed(html)
        return stripper.get_text()
    except Exception:
        # Last-resort fallback — should rarely trigger with stdlib parser
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        return " ".join(text.split())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def _imap_connection(readonly: bool = True):
    """Open a short-lived IMAP connection, log in, and yield the client."""
    client = IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True)
    try:
        client.login(ICLOUD_IMAP_USER, ICLOUD_APP_PASSWORD)
        yield client
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _smtp_send(msg: EmailMessage) -> None:
    """Send an EmailMessage via iCloud SMTP."""
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(ICLOUD_EMAIL, ICLOUD_APP_PASSWORD)
        smtp.send_message(msg)


def _extract_text(raw_bytes: bytes) -> str:
    """Parse a raw email and return the plain-text body (or stripped HTML)."""
    parser = BytesParser(policy=policy.default)
    msg = parser.parsebytes(raw_bytes)

    body = msg.get_body(preferencelist=("plain",))
    if body is not None:
        content = body.get_content()
        if isinstance(content, str):
            return content.strip()

    body = msg.get_body(preferencelist=("html",))
    if body is not None:
        content = body.get_content()
        if isinstance(content, str):
            return _strip_html(content)

    return ""


def _header(raw_bytes: bytes, name: str) -> str:
    """Extract a single header value from raw bytes."""
    parser = BytesParser(policy=policy.default)
    msg = parser.parsebytes(raw_bytes)
    return str(msg.get(name, ""))


def _snippet(text: str, length: int = 120) -> str:
    """Return the first `length` characters of text as a snippet."""
    text = " ".join(text.split())
    return text[:length] + "..." if len(text) > length else text


def _message_summary(uid: int, raw_bytes: bytes) -> dict:
    """Build a summary dict from raw message bytes."""
    return {
        "id": uid,
        "from": _header(raw_bytes, "From"),
        "to": _header(raw_bytes, "To"),
        "subject": _header(raw_bytes, "Subject"),
        "date": _header(raw_bytes, "Date"),
        "snippet": _snippet(_extract_text(raw_bytes)),
    }


def _build_email(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> EmailMessage:
    """Construct an EmailMessage ready to send or save as draft."""
    msg = EmailMessage()
    msg["From"] = ICLOUD_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now().astimezone())
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    msg.set_content(body)
    return msg


def _find_special_folder(client: IMAPClient, flag: str, fallback: str) -> str:
    """Return the folder name matching an IMAP special-use flag, or fallback."""
    flag_lower = flag.lower()
    for folder_flags, _delimiter, name in client.list_folders():
        if any(str(f).lower() == flag_lower for f in folder_flags):
            return name
    return fallback


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_mailboxes() -> list[dict]:
    """List all IMAP mailboxes/folders in the account.

    Returns a list of objects with 'name' and 'flags' for each mailbox.
    """
    with _imap_connection() as client:
        folders = client.list_folders()
        return [
            {"name": name, "flags": [str(f) for f in flags]}
            for flags, _delimiter, name in folders
        ]


@mcp.tool()
def list_messages(mailbox: str = "INBOX", count: int = 20) -> list[dict]:
    """List recent messages from a mailbox.

    Args:
        mailbox: IMAP mailbox name (default "INBOX").
        count: Number of recent messages to return (default 20, max 100).
    """
    count = min(max(1, count), 100)
    with _imap_connection() as client:
        client.select_folder(mailbox, readonly=True)
        uids = client.search(["ALL"])
        uids = uids[-count:]
        if not uids:
            return []
        fetched = client.fetch(uids, ["RFC822"])
        results = [
            _message_summary(uid, fetched[uid][b"RFC822"])
            for uid in uids
            if uid in fetched and b"RFC822" in fetched[uid]
        ]
        results.reverse()  # newest first
        return results


@mcp.tool()
def search_messages(
    mailbox: str = "INBOX",
    from_addr: str | None = None,
    to_addr: str | None = None,
    subject: str | None = None,
    keyword: str | None = None,
    since: str | None = None,
    before: str | None = None,
    count: int = 20,
) -> list[dict]:
    """Search for messages matching the given criteria.

    Args:
        mailbox: IMAP mailbox to search (default "INBOX").
        from_addr: Filter by sender address.
        to_addr: Filter by recipient address.
        subject: Filter by subject text.
        keyword: Search for keyword in the message body.
        since: Messages since this date (YYYY-MM-DD).
        before: Messages before this date (YYYY-MM-DD).
        count: Max results to return (default 20, max 100).
    """
    count = min(max(1, count), 100)
    criteria: list = []

    if from_addr:
        criteria.extend(["FROM", from_addr])
    if to_addr:
        criteria.extend(["TO", to_addr])
    if subject:
        criteria.extend(["SUBJECT", subject])
    if keyword:
        criteria.extend(["BODY", keyword])
    if since:
        try:
            dt = datetime.strptime(since, "%Y-%m-%d")
            criteria.extend(["SINCE", dt.strftime("%d-%b-%Y")])
        except ValueError:
            pass
    if before:
        try:
            dt = datetime.strptime(before, "%Y-%m-%d")
            criteria.extend(["BEFORE", dt.strftime("%d-%b-%Y")])
        except ValueError:
            pass

    if not criteria:
        criteria = ["ALL"]

    with _imap_connection() as client:
        client.select_folder(mailbox, readonly=True)
        uids = client.search(criteria)
        uids = uids[-count:]
        if not uids:
            return []
        fetched = client.fetch(uids, ["RFC822"])
        results = [
            _message_summary(uid, fetched[uid][b"RFC822"])
            for uid in uids
            if uid in fetched and b"RFC822" in fetched[uid]
        ]
        results.reverse()
        return results


@mcp.tool()
def read_message(message_id: int, mailbox: str = "INBOX") -> dict:
    """Read a full email message by its UID.

    Args:
        message_id: The UID of the message to read.
        mailbox: The mailbox containing the message (default "INBOX").

    Raises:
        ValueError: If the message is not found.
    """
    with _imap_connection() as client:
        client.select_folder(mailbox, readonly=True)
        fetched = client.fetch([message_id], ["RFC822"])
        raw = fetched.get(message_id, {}).get(b"RFC822", b"")
        if not raw:
            raise ValueError(f"Message {message_id} not found in {mailbox}")

        parser = BytesParser(policy=policy.default)
        msg = parser.parsebytes(raw)

        headers = {
            "from": str(msg.get("From", "")),
            "to": str(msg.get("To", "")),
            "cc": str(msg.get("Cc", "")),
            "bcc": str(msg.get("Bcc", "")),
            "subject": str(msg.get("Subject", "")),
            "date": str(msg.get("Date", "")),
            "message_id": str(msg.get("Message-ID", "")),
            "reply_to": str(msg.get("Reply-To", "")),
        }

        attachments = [
            {
                "filename": part.get_filename() or "unnamed",
                "content_type": part.get_content_type(),
            }
            for part in msg.walk()
            if "attachment" in str(part.get("Content-Disposition", ""))
        ]

        return {
            "id": message_id,
            "headers": headers,
            "body": _extract_text(raw),
            "attachments": attachments,
        }


@mcp.tool()
def mark_as_read(message_ids: list[int], mailbox: str = "INBOX") -> dict:
    """Mark specific messages as read.

    Args:
        message_ids: List of message UIDs to mark as read.
        mailbox: The mailbox containing the messages (default "INBOX").

    Raises:
        ValueError: If message_ids is empty.
    """
    if not message_ids:
        raise ValueError("message_ids must not be empty")
    with _imap_connection() as client:
        client.select_folder(mailbox, readonly=False)
        client.add_flags(message_ids, [b"\\Seen"])
        return {"status": "ok", "marked_read": len(message_ids)}


@mcp.tool()
def mark_as_unread(message_ids: list[int], mailbox: str = "INBOX") -> dict:
    """Mark specific messages as unread.

    Args:
        message_ids: List of message UIDs to mark as unread.
        mailbox: The mailbox containing the messages (default "INBOX").

    Raises:
        ValueError: If message_ids is empty.
    """
    if not message_ids:
        raise ValueError("message_ids must not be empty")
    with _imap_connection() as client:
        client.select_folder(mailbox, readonly=False)
        client.remove_flags(message_ids, [b"\\Seen"])
        return {"status": "ok", "marked_unread": len(message_ids)}


@mcp.tool()
def delete_messages(message_ids: list[int], mailbox: str = "INBOX") -> dict:
    """Move specific messages to Trash.

    Moves to the account Trash folder rather than immediately expunging,
    preserving a recovery window.

    Args:
        message_ids: List of message UIDs to delete.
        mailbox: The mailbox containing the messages (default "INBOX").

    Raises:
        ValueError: If message_ids is empty.
    """
    if not message_ids:
        raise ValueError("message_ids must not be empty")
    with _imap_connection() as client:
        trash_folder = _find_special_folder(client, "\\\\Trash", "Trash")
        client.select_folder(mailbox, readonly=False)
        client.move(message_ids, trash_folder)
        return {
            "status": "ok",
            "deleted": len(message_ids),
            "moved_to": trash_folder,
        }


@mcp.tool()
def move_messages(
    message_ids: list[int],
    destination: str,
    mailbox: str = "INBOX",
) -> dict:
    """Move specific messages to another mailbox.

    Args:
        message_ids: List of message UIDs to move.
        destination: Name of the destination mailbox.
        mailbox: Source mailbox (default "INBOX").

    Raises:
        ValueError: If message_ids is empty.
    """
    if not message_ids:
        raise ValueError("message_ids must not be empty")
    with _imap_connection() as client:
        client.select_folder(mailbox, readonly=False)
        client.move(message_ids, destination)
        return {
            "status": "ok",
            "moved": len(message_ids),
            "from": mailbox,
            "to": destination,
        }


@mcp.tool()
def send_message(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> dict:
    """Send an email via iCloud SMTP.

    Args:
        to: Recipient email address (comma-separated for multiple).
        subject: Email subject line.
        body: Plain text email body.
        cc: CC recipients (comma-separated, optional).
        bcc: BCC recipients (comma-separated, optional).

    Raises:
        Exception: If the message fails to send.
    """
    msg = _build_email(to, subject, body, cc=cc, bcc=bcc)
    _smtp_send(msg)  # raises on failure — callers see real errors
    return {"status": "sent", "to": to, "subject": subject}


@mcp.tool()
def create_draft(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
) -> dict:
    """Create a draft email in the Drafts mailbox.

    Args:
        to: Recipient email address (comma-separated for multiple).
        subject: Email subject line.
        body: Plain text email body.
        cc: CC recipients (comma-separated, optional).
        bcc: BCC recipients (comma-separated, optional).
    """
    msg = _build_email(to, subject, body, cc=cc, bcc=bcc)
    raw_bytes = msg.as_bytes()

    with _imap_connection() as client:
        drafts_folder = _find_special_folder(client, "\\\\Drafts", "Drafts")
        client.append(drafts_folder, raw_bytes)
        return {
            "status": "draft_created",
            "folder": drafts_folder,
            "to": to,
            "subject": subject,
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
