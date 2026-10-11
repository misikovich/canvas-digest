# UiS Canvas assistant

Keeps a local copy of your UiS Canvas (timetable, deadlines, grades, announcements),
serves it to an agent over MCP, and sends Telegram alerts through OpenClaw.

```
OpenClaw command job (*/30)  ── uis tick ──▶ stdout ──▶ Telegram   (fixed alerts, no LLM)
OpenClaw agent jobs (07:00, Sun 19:00) ── MCP digest ──▶ Telegram  (daily plan / weekly overview)
Chat with OpenClaw ── MCP tools ──▶ SQLite store (data/uis.db)
```

## Setup (on the OpenClaw server)

```bash
git clone <this repo> ~/uis-assistant && cd ~/uis-assistant
uv sync
uv run uis init          # paste a Canvas token: Account → Settings → + New Access Token
TELEGRAM_CHAT_ID=<your chat id> deploy/openclaw-setup.sh   # DRY_RUN=1 to preview
openclaw mcp status --verbose
```

`uis init` writes `data/config.toml`, stores the token in `data/canvas_token` (mode 600),
checks it, and runs the first sync. The first sync is a baseline: it doesn't notify.

## Commands

| Command | What it does |
|---|---|
| `uis sync` | Fetch Canvas and update the store (exit 1 on failure) |
| `uis tick` | Sync, then print the Telegram message or `NO_REPLY` (always exit 0 unless it crashes) |
| `uis notify --dry-run` | Show pending alerts without marking them sent |
| `uis digest [--week]` | Print the digest the morning/weekly jobs use |
| `uis status` | Last sync, errors, semester window, item counts |
| `uis mcp` | MCP server on stdio |

Logs go to `data/uis.log` (`-v` also logs to stderr).

## What triggers an alert

- New deadline, due date changed, deadline removed
- Graded / new feedback
- Lecture moved (time or room), removed from the timetable, or a new one within 7 days
- New announcement (with excerpt and link)
- Reminders 48h and 6h before an unsubmitted deadline
- Canvas token rejected (immediately) or 3 failed syncs in a row; and recovery

Quiet hours (23–07 by default) hold everything except reminders due before morning.
Settings live in `data/config.toml`.

## MCP tools

`upcoming`, `deadlines`, `list_new`, `search`, `get_item`, `digest`, `courses`, `status`,
`sync_now`. All read-only towards Canvas. Announcement/assignment bodies are returned inside
`<untrusted>` tags. The agent-side guidance is in `openclaw/skills/uis-canvas/SKILL.md`.

## Development

```bash
uv run pytest
npx @modelcontextprotocol/inspector uv run uis mcp
```

Tests use small anonymized fixtures in `tests/fixtures`. Real captures go in `data/`
(gitignored).
