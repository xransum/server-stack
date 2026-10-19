# Workers — Job System

This directory contains isolated job definitions for remaining homelab work.
Each worker is a self-contained session that does one job and nothing else.

## How it works

1. **Start a worker session** — paste the worker's opening prompt verbatim
2. **Worker does the job** — reads only their own file, no other context needed
3. **Worker updates their file** — adds a `## Status` section with what was done and any decisions that shifted
4. **Worker commits + pushes** — `git add docs/workers/worker-N-*.md && git commit -m "worker-N: completed" && git push`
5. **Worker asks you to confirm** — you verify, then close the session
6. **Parent session syncs** — paste `docs/workers/state.md` into the parent session to get back in sync

## Worker registry

| Worker | Job | Status | Trigger |
|---|---|---|---|
| [Worker 1](worker-1-gpu.md) | PCIe passthrough + NVIDIA drivers + Plex NVENC | ⏳ pending | RTX 2070 physically in Proxmox machine |
| [Worker 2](worker-2-decommission.md) | Decommission serverhub | ✅ completed | — |
| [Worker 3](worker-3-gameservers.md) | Create gameservers-vm + Pelican Panel | ⏳ pending | Anytime (independent) |
| [Worker 4](worker-4-devboxes.md) | Create devbox-template VM | ⏳ pending | Anytime (independent) |
| [Worker 5](worker-5-portainer.md) | Portainer CE Docker management UI | ⏳ pending | Anytime (independent) |

## Parent session

The parent session (`state.md`) tracks the big picture.
When you return to the parent, paste the contents of `state.md` to re-sync.
