# iCloud Mail MCP Server

A local MCP server for interacting with iCloud Mail via IMAP and SMTP.

## Setup

1. Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

You need an **app-specific password** from [appleid.apple.com](https://appleid.apple.com/) (Account Security > App-Specific Passwords).

3. Add to your Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "icloud-mail": {
      "command": "/Users/buford/dev/tools/icloud-mail-mcp/.venv/bin/python",
      "args": ["/Users/buford/dev/tools/icloud-mail-mcp/server.py"]
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
| `ICLOUD_EMAIL` | Your iCloud email address |
| `ICLOUD_APP_PASSWORD` | App-specific password from Apple ID settings |
