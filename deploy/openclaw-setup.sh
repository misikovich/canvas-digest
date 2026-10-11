#!/usr/bin/env bash
# Register the uis MCP server, install the skill, and create the OpenClaw automations.
#
# Usage (on the server, from the repo root, after `uv sync && uv run uis init`):
#   TELEGRAM_CHAT_ID=123456789 deploy/openclaw-setup.sh
#
# Optional env:
#   UIS_PROJECT     repo path (default: this checkout)
#   OPENCLAW_SKILLS skills dir (default: ~/.openclaw/skills)
#   DRY_RUN=1       print the commands instead of running them
#
# The OpenClaw CLI evolves quickly: if a command is rejected, compare with
# `openclaw mcp --help` and `openclaw automations create --help`.
set -euo pipefail

: "${TELEGRAM_CHAT_ID:?set TELEGRAM_CHAT_ID to your Telegram chat id}"
UIS_PROJECT="${UIS_PROJECT:-$(cd "$(dirname "$0")/.." && pwd)}"
OPENCLAW_SKILLS="${OPENCLAW_SKILLS:-$HOME/.openclaw/skills}"
UV="$(command -v uv)"
TZ_NAME="Europe/Oslo"

run() {
  if [[ "${DRY_RUN:-}" == 1 ]]; then echo "+ $(printf '%q ' "$@")"; else "$@"; fi
}

echo "== Checking the project works"
run "$UV" run --project "$UIS_PROJECT" uis status

echo "== Registering MCP server 'uis' (stdio)"
run openclaw mcp set uis "$(printf '{"command":"%s","args":["run","--quiet","--frozen","--project","%s","uis","mcp"]}' "$UV" "$UIS_PROJECT")"

echo "== Installing skill"
run mkdir -p "$OPENCLAW_SKILLS"
run cp -R "$UIS_PROJECT/openclaw/skills/uis-canvas" "$OPENCLAW_SKILLS/"

echo "== Creating automations"
# Fixed alerts: no LLM. Prints a message or NO_REPLY (suppressed by OpenClaw).
run openclaw automations create "*/30 * * * *" \
  --name "UiS tick" \
  --tz "$TZ_NAME" \
  --command-argv "$(printf '["%s","run","--quiet","--frozen","--project","%s","uis","tick"]' "$UV" "$UIS_PROJECT")" \
  --announce --channel telegram --to "$TELEGRAM_CHAT_ID"

run openclaw automations create "0 7 * * *" \
  "Morning plan: use the uis-canvas skill. Call the uis MCP tool digest with period=day and send me a short plan for today." \
  --name "UiS morning" \
  --tz "$TZ_NAME" \
  --announce --channel telegram --to "$TELEGRAM_CHAT_ID"

run openclaw automations create "0 19 * * 0" \
  "Weekly overview: use the uis-canvas skill. Call the uis MCP tool digest with period=week and send me an overview of the coming week." \
  --name "UiS weekly" \
  --tz "$TZ_NAME" \
  --announce --channel telegram --to "$TELEGRAM_CHAT_ID"

echo "== Done. Verify with: openclaw mcp status --verbose"
