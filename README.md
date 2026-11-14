# NEXUS v3.0 — Telegram Automation & Persistent Profile Agent

## Environment Variables
- `TG_BOT_TOKEN`: Your Telegram Bot Token from `@BotFather`
- `TG_CHAT_ID`: Your numeric Telegram Chat ID from `@userinfobot`

## Quick Start (Linux / VPS / Codespaces)
```bash
pip install -r requirements.txt
playwright install chromium
export TG_BOT_TOKEN="YOUR_BOT_TOKEN"
export TG_CHAT_ID="YOUR_CHAT_ID"
xvfb-run -a python nexus_agent.py
```

## Telegram Commands
- `/start` — Show help menu
- `/run <url>` — Run workflow on default profile (`p1`)
- `/run <profile> <url>` — Run workflow on a specific profile
- `/proxy <profile> <http://user:pass@ip:port>` — Attach proxy to profile (`none` to clear)
- `/profiles` — List all saved profiles and proxies
- `/ss` — Capture instant live screenshot
