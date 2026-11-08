# TrueNAS

TrueNAS Scale NAS appliance providing NFS-backed storage for all media on media-vm.
It is a separate physical machine on the LAN — not a VM.

- **LAN IP:** 192.168.1.164
- **Tailscale IP:** 100.67.119.85
- **SSH:** `ssh truenas` (homelab key, `truenas_admin` user)

---

## NFS Exports

All shares are exported to `192.168.1.0/24` and mounted on media-vm at boot via
`/etc/fstab`. The mount points inside containers match the host paths exactly (bind
mounts, not remapped).

| TrueNAS dataset | Mount on media-vm | Used by |
|---|---|---|
| `/mnt/media/tv` | `/mnt/media/tv` | Sonarr (import destination) |
| `/mnt/media/movies` | `/mnt/media/movies` | Radarr (import destination) |
| `/mnt/media/downloads` | `/mnt/media/downloads` | TorBoxarr (staging + completed), Shelfmark (books) |
| `/mnt/media/books` | `/mnt/media/books` | Calibre-Web, Shelfmark (read-only) |
| `/mnt/media/audiobooks` | `/mnt/media/audiobooks` | Audiobook player |
| `/mnt/media/music` | `/mnt/media/music` | Music player |
| `/mnt/media/gameservers` | (not mounted on media-vm) | gameservers-vm only |

---

## UID Alignment — Critical Gotcha

**This is the most important thing to understand about the TrueNAS + media-vm setup.**

### The problem

NFS maps file ownership by numeric UID, not by username. The two systems have
mismatched UIDs:

| System | User | UID |
|---|---|---|
| TrueNAS | `truenas_admin` | 950 |
| media-vm / Sonarr / Radarr | `ransum` / `abc` | 1000 |

Sonarr and Radarr run internally as user `abc` (uid 1000), which maps to `ransum`
on the host. When TrueNAS creates a directory (uid 950, mode `drwxr-xr-x`), Sonarr
cannot write into it — the import silently fails with "Failed to import episode/movie"
and no useful error in the UI. The only evidence is a stack trace in
`/config/logs/sonarr.txt` showing `UnauthorizedAccessException: Permission denied`
on the destination path.

This affected **1,686+ directories** across `/mnt/media/tv` and `/mnt/media/movies`
before it was discovered and fixed in November 2026 via a MobLand S02 import failure.

### The fix (applied 2026-11-07)

**On TrueNAS:**
1. Created a local user `ransum` with UID `1000`, no password, no shell, no SSH access
   — it exists solely as a UID mapping target
2. Set **Mapall User = ransum** on all NFS shares (Shares → NFS → Edit each share)
   This remaps all NFS client writes to uid 1000 regardless of the actual client user,
   so both TrueNAS-created and Sonarr-created directories are owned by uid 1000

**On Sonarr and Radarr:**
- Enabled **Set Permissions** (Settings → Media Management → Permissions)
- `chmod Folder = 755` (`drwxr-xr-x`) — Sonarr/Radarr apply this on every import
- This ensures newly created directories are always writable by their owner (uid 1000)

**One-time remediation:**
Fixed all existing directories that had wrong permissions:
```bash
# uid 950 owned (TrueNAS-created) — fixed from TrueNAS
find /mnt/media/tv /mnt/media/movies -type d ! -perm -o+w -exec chmod o+w {} \;

# uid 1000 owned (Sonarr/Radarr-created) — fixed from media-vm (no sudo needed)
find /mnt/media/tv /mnt/media/movies -type d ! -perm -o+w -user ransum -exec chmod o+w {} \;
```

### Adding new NFS shares

When creating a new NFS share on TrueNAS, always set **Mapall User = ransum** before
mounting it on media-vm. If you forget, any directory TrueNAS creates on that share
will be uid 950 and Sonarr/Radarr will silently fail to import into it.

---

## Sonarr / Radarr Media Management Settings

Both Sonarr and Radarr must have these settings applied:

**Settings → Media Management → Permissions:**
- **Set Permissions:** ✅ enabled
- **chmod Folder:** `755`
- **chown Group:** (leave blank)

This only takes effect on files/dirs where Sonarr/Radarr is the owner (uid 1000).
It does not retroactively fix dirs owned by uid 950 — that requires the mapall fix
described above.

---

## Decisions & Alternatives Rejected

**Why mapall instead of per-user mapping or ACLs?**
TrueNAS Scale supports NFSv4 ACLs but they add complexity and the UI for managing
them is non-trivial. `mapall` is a single setting per share that completely solves
the uid mismatch with no ongoing maintenance. The tradeoff is that all client
writes appear as uid 1000 regardless of actual user — acceptable for a single-user
homelab where only one client (media-vm) writes to these shares.

**Why not set TrueNAS uid to 1000?**
`truenas_admin` is a system account managed by TrueNAS Scale — its UID is fixed at
950 and cannot be changed without breaking TrueNAS internals.

**Why not run Sonarr/Radarr as uid 950?**
The linuxserver.io Docker images use `PUID`/`PGID` env vars. Setting `PUID=950`
would work but then media-vm's `ransum` user (uid 1000) would lose access to files
Sonarr creates. The uid 1000 ↔ ransum mapping is cleaner and consistent with how
the rest of the system is set up.

---

## Known Limitations

- **NFS root squash is enabled** on all shares. This means `sudo chmod` from media-vm
  over NFS is remapped to `nobody` and fails. Any permission fixes on uid 1000 owned
  files must be run as `ransum` directly (no sudo), and permission fixes on uid 950
  files must be run from TrueNAS directly.
- **1GbE bottleneck** — TrueNAS NIC (`enp4s0`) negotiates at 1GbE because the switch
  (TP-Link TL-SG1016) is 1GbE-only. Max NFS throughput is ~111 MB/s. A 10GbE switch
  (e.g. TP-Link TL-SX1008) would unlock the full 10GbE capability of the TrueNAS NIC.
  See `docs/proxmox-compute.md` for more detail.
