# iCloud Mail MCP Server

A local [MCP](https://modelcontextprotocol.io/) server for interacting with iCloud Mail via IMAP and SMTP, built with [FastMCP](https://github.com/jlowin/fastmcp).

## Setup

1. Clone the repo and create a virtual environment:

```bash
git clone https://github.com/bufordeeds/icloud-mail-mcp.git
cd icloud-mail-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

You need an **app-specific password** from [appleid.apple.com](https://appleid.apple.com/) under Sign-In and Security > App-Specific Passwords.

3. Add to Claude Code (user scope so it's available everywhere):

```bash
claude mcp add -s user --transport stdio icloud-mail -- /path/to/icloud-mail-mcp/.venv/bin/python /path/to/icloud-mail-mcp/server.py
```

Or add to Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "icloud-mail": {
      "command": "/path/to/icloud-mail-mcp/.venv/bin/python",
      "args": ["/path/to/icloud-mail-mcp/server.py"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `list_mailboxes` | List all IMAP folders |
| `list_messages` | List recent messages from a mailbox |
| `search_messages` | Search messages by from, to, subject, date, keyword |
| `read_message` | Read a full message by ID |
| `send_message` | Send an email |
| `create_draft` | Save a draft email |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ICLOUD_EMAIL` | Your iCloud email address (used for SMTP and as the "From" address) |
| `ICLOUD_IMAP_USER` | IMAP login username (defaults to `ICLOUD_EMAIL` if not set) |
| `ICLOUD_APP_PASSWORD` | App-specific password from Apple ID settings |

## Notes

- iCloud+ custom email domain addresses (e.g. `you@yourdomain.com`) are aliases on your `@icloud.com` mailbox. IMAP access shows the unified inbox.
- If you use a custom domain with iCloud+, consider setting up **forwarding** in iCloud Mail settings to route mail to Gmail for easier IMAP access, since Apple's custom domain IMAP support can be inconsistent.
