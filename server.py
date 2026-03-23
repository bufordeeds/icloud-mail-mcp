"""iCloud Mail MCP Server — IMAP + SMTP access via FastMCP."""

import os
import smtplib
from contextlib import contextmanager
from datetime import datetime
from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, parseaddr

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

mcp = FastMCP("icloud-mail")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def _imap_connection():
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


def _smtp_send(msg: EmailMessage):
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

    # Prefer plain text
    body = msg.get_body(preferencelist=("plain",))
    if body is not None:
        content = body.get_content()
        if isinstance(content, str):
            return content.strip()

    # Fall back to HTML, strip tags naively
    body = msg.get_body(preferencelist=("html",))
    if body is not None:
        content = body.get_content()
        if isinstance(content, str):
            import re
            text = re.sub(r"<[^>]+>", " ", content)
            text = re.sub(r"\s+", " ", text)
            return text.strip()

    return ""


def _header(raw_bytes: bytes, name: str) -> str:
    """Extract a single header value from raw bytes."""
    parser = BytesParser(policy=policy.default)
    msg = parser.parsebytes(raw_bytes)
    val = msg.get(name, "")
    return str(val)


def _snippet(text: str, length: int = 120) -> str:
    """Return the first `length` characters of text as a snippet."""
    text = " ".join(text.split())  # collapse whitespace
    if len(text) > length:
        return text[:length] + "..."
    return text


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
        results = []
        for flags, delimiter, name in folders:
            results.append({
                "name": name,
                "flags": [str(f) for f in flags],
            })
        return results


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
        # Get the most recent UIDs
        uids = client.search(["ALL"])
        uids = uids[-count:]  # last N
        if not uids:
            return []
        # Fetch headers + a bit of body for snippets
        fetched = client.fetch(uids, ["RFC822"])
        results = []
        for uid in uids:
            raw = fetched.get(uid, {}).get(b"RFC822", b"")
            if raw:
                results.append(_message_summary(uid, raw))
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
        uids = uids[-count:]  # take last N (most recent)
        if not uids:
            return []
        fetched = client.fetch(uids, ["RFC822"])
        results = []
        for uid in uids:
            raw = fetched.get(uid, {}).get(b"RFC822", b"")
            if raw:
                results.append(_message_summary(uid, raw))
        results.reverse()
        return results


@mcp.tool()
def read_message(message_id: int, mailbox: str = "INBOX") -> dict:
    """Read a full email message by its ID (UID).

    Args:
        message_id: The UID of the message to read.
        mailbox: The mailbox containing the message (default "INBOX").
    """
    with _imap_connection() as client:
        client.select_folder(mailbox, readonly=True)
        fetched = client.fetch([message_id], ["RFC822"])
        raw = fetched.get(message_id, {}).get(b"RFC822", b"")
        if not raw:
            return {"error": f"Message {message_id} not found in {mailbox}"}

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

        body_text = _extract_text(raw)

        # List attachments
        attachments = []
        for part in msg.walk():
            content_disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in content_disposition:
                filename = part.get_filename() or "unnamed"
                attachments.append({
                    "filename": filename,
                    "content_type": part.get_content_type(),
                })

        return {
            "id": message_id,
            "headers": headers,
            "body": body_text,
            "attachments": attachments,
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
    """
    msg = _build_email(to, subject, body, cc=cc, bcc=bcc)
    try:
        _smtp_send(msg)
        return {"status": "sent", "to": to, "subject": subject}
    except Exception as e:
        return {"status": "error", "error": str(e)}


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
        # Find the Drafts folder — iCloud uses "Drafts"
        drafts_folder = "Drafts"
        folders = client.list_folders()
        for flags, delimiter, name in folders:
            if "\\Drafts" in [str(f) for f in flags] or name.lower() == "drafts":
                drafts_folder = name
                break

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
