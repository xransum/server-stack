# Session Compaction — Parent Orchestrator Context

## Purpose
This file is the compaction context for the parent orchestrator session.
Paste this into a new session along with docs/workers/state.md to resume as parent.

---

## Parent Session Role

This session is the **orchestrator/parent**. Its only jobs are:
1. Read `docs/workers/state.md` to know current worker status
2. Sync state.md when workers report completion
3. Create new worker files when new jobs are identified
4. Never do infrastructure work directly — that's for workers

---

## How to resume this session

Paste the following into a new chat:

```
You are the parent orchestrator for the xransum homelab server-stack project.
Your job is to track worker status, sync state.md when workers complete jobs,
and create new worker files when new jobs are identified.
Do NOT do infrastructure work directly — delegate to workers.

Read docs/workers/state.md for current status.
Read docs/workers/README.md for the worker system overview.
Read AGENTS.md for all project conventions and security rules.

Repo: https://github.com/xransum/server-stack
Local: ~/Documents/Gits/server-stack

[paste contents of docs/workers/state.md here]
```

---

## Infrastructure Summary

| Host | LAN IP | Tailscale IP | SSH alias |
|---|---|---|---|
| media-vm | 192.168.1.169 | 100.107.1.14 | `ssh media-vm` / `ssh media-vm-ts` |
| proxmox | 192.168.1.168 | 100.77.224.51 | `ssh proxmox` / `ssh proxmox-ts` |
| truenas | 192.168.1.164 | 100.67.119.85 | `ssh truenas` / `ssh truenas-ts` |
| serverhub | 192.168.1.166 | none | DECOMMISSIONED — powered off |

SSH key: `~/.ssh/homelab` (ed25519) on all hosts.
Secrets in `~/.env` on Windows: `SSH_PASSWORD`, `CF_DDNS_TOKEN`, `DISCORD_WEBHOOK_URL`.
Never log, print, or commit values from `~/.env`.

---

## Key Decisions Made This Session

- Plex NOT behind CF tunnel (ToS). Direct port forward 32400+443 → media-vm. NPM handles SSL with Let's Encrypt cert.
- cloudflare-ddns container keeps grey-cloud records updated (plex + future game servers)
- Single `homelab` ed25519 key for all homelab hosts
- Worker system: isolated sessions, each updates own file, parent syncs state.md
- API keys rotated after Worker 2 accidentally committed them — NEVER commit secrets
- Each service has its own Discord webhook — none are shared — never commit webhook URLs
- Overseerr being migrated to Seerr (Worker 6) — same port 5055, no routing changes

---

## Credentials Location

- `~/.env` on Windows — SSH_PASSWORD, CF_DDNS_TOKEN, DISCORD_WEBHOOK_URL
- API keys: get from each app's Settings → General
- Compose secrets: `/opt/media/server-stack/compose/.env` on media-vm (gitignored)

---

## Active Workers

See `docs/workers/state.md` for current status of all workers.
See `docs/workers/README.md` for how the system works.
