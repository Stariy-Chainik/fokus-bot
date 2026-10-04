---
name: Deployment — SSH to Hetzner VPS, systemd-managed
description: How fokus-bot is deployed and run in production (SSH + rsync from local, systemd service on server)
type: project
originSessionId: 2307c0b0-35a0-4605-9391-097ec66750f1
---
Fokus-bot deployed directly over SSH from the user's local machine to a Hetzner VPS. Not Railway, despite README/docs/architecture.md hints.

**Server (current)**
- Host: `root@178.104.240.252` (Hetzner CX22, Ubuntu 24.04, Nuremberg, hostname `ubuntu-4gb-nbg1-1`)
- Deploy path: `/opt/fokus-bot`
- Venv: `/opt/fokus-bot/.venv` (Python 3.12.3, `/opt/fokus-bot/.venv/bin/python`)
- Service: `fokus-bot.service` (systemd, `/etc/systemd/system/fokus-bot.service`)
- Run command: `/opt/fokus-bot/.venv/bin/python -m bot`
- Key auth set up from stas's Mac — no password needed.

**Migration history**
- 2026-04-22: migrated from Timeweb (`147.45.146.247`) to Hetzner Nuremberg.
  **Why:** Roskomnadzor/DPI on Timeweb started blocking `api.telegram.org` — DNS hijacked to 198.18.0.5, TCP to real IPs dropped. Bot crash-looped 335× before migration. Hetzner Germany has clean route to Telegram.
  **How to apply:** do not suggest Russian hosting for bot services that need Telegram API.
- Old server at `147.45.146.247` disabled (`systemctl disable fokus-bot`) but still reachable via SSH — keep ~1 week as backup, then decommission.

**Deploy flow**
- `./scripts/deploy.sh` from local — rsync to `$HOST:/opt/fokus-bot/`, then `ssh $HOST systemctl restart fokus-bot`, prints last 20 journal lines.
- `$HOST` inside deploy.sh = `root@178.104.240.252` (updated 2026-04-22).
- Secrets live in `/opt/fokus-bot/.env` on the server (not in git). Google service-account creds are inline in `.env` as `GOOGLE_CREDENTIALS_JSON` — no separate credentials.json file.
- Logs: `journalctl -u fokus-bot -n 50 --no-pager` or `journalctl -u fokus-bot -f`.

**How to apply**
- "Deploy" / "restart the bot" → run `./scripts/deploy.sh` from `/Users/stas/Documents/fokus-bot/`, or `ssh root@178.104.240.252 'systemctl restart fokus-bot'` for pure restart.
- Ignore Railway/Procfile instructions for deploy.
