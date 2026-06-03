# Storage

NAS-backed persistent storage for the homelab. All media, game world saves,
S3 backing store, and dev VM disks live on the NAS. The compute VMs are
stateless with respect to data — a full wipe of the compute server loses
nothing except running state.

---

## Why a dedicated NAS

Running storage on the same machine as compute creates a single point of
failure and makes hardware upgrades painful (can't upgrade compute without
touching storage). A dedicated NAS means:

- Compute server can be rebuilt, reinstalled, or replaced without touching
  a single media file or game world save
- Storage can be expanded independently of compute
- ZFS scrubs and resilver operations don't compete with Plex/game CPU budget
- TrueNAS provides a stable, purpose-built UI for pool management, snapshots,
  and NFS export config

---

## OS: TrueNAS Scale

TrueNAS Scale is Debian-based with native ZFS, a web UI covering pool
management/snapshots/NFS/SMB exports, and a built-in app catalog for
optional services (MinIO for S3, etc.). Chosen over alternatives:

| Option | Why rejected |
|---|---|
| Plain Debian + ZFS + Cockpit | More control, but you're reinventing the TrueNAS UI for no gain on a storage-only box |
| Unraid | License cost, mergerFS-style storage (no ZFS native), less battle-tested with ZFS compared to TrueNAS |
| FreeNAS (legacy) | Superseded by TrueNAS |

---

## ZFS pool layout

### Why mirror vdevs, not RAIDZ

**This is the most important storage decision in the plan. Read it before
buying drives.**

RAIDZ (1/2/3) vdev shape is **fixed at creation**. A 4-drive RAIDZ1 vdev
cannot become a 6-drive RAIDZ2 without destroying and recreating the pool.
Expansion means adding a completely new RAIDZ vdev (which must match the
original stripe width), or rebuilding from scratch.

Mirror vdevs are fully flexible. You start with one 2-drive mirror (one vdev).
When you're ready to add capacity, you run `zpool add <pool> mirror <dev1>
<dev2>` and the pool grows immediately — same mount point, same NFS export,
same Docker volume paths, zero downtime. Add as many mirror pairs as your
chassis has bays.

```
# Example: media pool growing over time

# Day 1 — 2 drives, 12TB usable
zpool create media mirror sda sdb
# /mnt/media is 12TB

# Month 3 — add a second pair, no downtime
zpool add media mirror sdc sdd
# /mnt/media is now 24TB

# Month 6 — add a third pair
zpool add media mirror sde sdf
# /mnt/media is now 36TB
```

TrueNAS wraps this in a GUI ("Add VDEV" → select two drives → confirm).
Nothing pointing at `/mnt/media` — NFS exports, Docker bind mounts, *arr
paths — ever needs to change.

Trade-off: mirror vdevs have 50% capacity overhead (2 drives per TB of
usable space) vs RAIDZ2's ~33% overhead on large stripe widths. For a
personal media server this is acceptable — drives are cheap relative to
the operational simplicity gained. If raw capacity is the priority,
mergerFS+SnapRAID can reach ~85% utilization, but SnapRAID parity is
rebuilt nightly (data written between runs is temporarily unprotected)
and TrueNAS doesn't manage it natively.

### Pool definitions

| Pool | Purpose | Starting drives | Topology | Usable (start) | Max usable (24-bay NAS) |
|---|---|---|---|---|---|
| `media` | All media + downloads | 2× 12TB | Mirror vdevs | 12TB | ~96TB (8 mirror pairs) |
| `s3` | S3/object storage backing | 2× 12TB | Mirror vdevs | 12TB | Expand as needed |
| `dev` | Devbox VM disks + snapshots | 2× 2TB | Mirror | 2TB | Fixed (2 drives) |
| `boot` | TrueNAS OS | 1–2× SSD (32GB+) | Mirror recommended | — | — |

> **MinIO deployment note:** The `s3` pool backs a native MinIO TrueNAS Scale
> app at `/mnt/s3/store`. MinIO is not part of the `media-vm` Docker Compose
> stack.

> **Note on the boot drive:** Do not use USB flash drives for the TrueNAS
> OS. They fail under constant writes within 1–2 years. Use a cheap SSD or
> NVMe (even 32GB). If the motherboard has M.2 slots, put TrueNAS there and
> leave all SATA/SAS bays for data drives.

> **Note on drive sizes:** ZFS uses the smaller drive size per vdev. If you
> add a 16TB pair to a pool of 12TB mirror vdevs, the new vdev contributes
> 12TB, not 16TB. Keep drive sizes consistent per vdev, or be aware of this
> when mixing sizes in the future.

---

## Dataset and mount layout

All pools are divided into ZFS datasets. Datasets are the unit of NFS
export, snapshot policy, and quota enforcement in TrueNAS.

```
media/
  downloads/
    complete/         ← download clients drop completed files here
    incomplete/       ← in-progress download temp space
  movies/             ← Radarr root folder
  tv/                 ← Sonarr root folder
  music/
  audiobooks/
  books/

s3/
  store/              ← MinIO backing store dataset

dev/
  backups/            ← long-term VM snapshots exported from Proxmox
  vmdisks/            ← active devbox VM disk images (iSCSI or NFS)
  gameservers/        ← game world saves, per-game subdirs
```

### The hardlink requirement — critical for *arr

Sonarr and Radarr "import" completed downloads by either **hardlinking**
or **copying** the file from the downloads directory to the media library.
Hardlinks are instant and use zero additional disk space (two directory
entries pointing at the same inode). Copies are slow and double your disk
usage until the original is deleted.

**Hardlinks only work within the same filesystem.** This means
`downloads/complete/` and `movies/`, `tv/`, etc. must all live on the
**same ZFS dataset** (or at minimum under the same pool mount point) — not
on separate pools or separate NFS mounts.

The layout above puts everything under `media/` for this reason. TrueNAS
exports `/mnt/media` as a **single NFS share**, mounted at the same path
inside both the media-vm and any container that needs it:

```
NAS exports:  /mnt/media  (entire media pool)
media-vm:     /mnt/media  (NFS mount, same path)
containers:   /mnt/media  (bind mount from VM, same path)
```

Sonarr and Radarr are configured with:
- Root folder: `/mnt/media/tv/` and `/mnt/media/movies/`
- Download client category paths: `/mnt/media/downloads/complete/`

Because these are all within the same NFS mount, hardlinks work and imports
are instant with no extra disk usage.

---

## NFS export configuration (TrueNAS)

Three NFS shares exported from TrueNAS:

| Share | Dataset | Client | Options |
|---|---|---|---|
| `/mnt/media` | `media` | media-vm IP | `rw, sync, no_subtree_check` |
| `/mnt/s3` | `s3/store` | media-vm IP | `rw, sync, no_subtree_check` |
| `/mnt/dev` | `dev` | Proxmox host IP | `rw, sync, no_subtree_check` |

Restrict exports to the specific VM/host IPs (not `*`) so the NAS is not
accidentally accessible to any device on the LAN.

---

## Snapshot policy

ZFS snapshots are near-instant and space-efficient (copy-on-write). TrueNAS
manages snapshot schedules via its UI.

Recommended schedule:

| Dataset | Hourly | Daily | Weekly | Monthly | Retention |
|---|---|---|---|---|---|
| `media/movies`, `media/tv` | No | Yes | Yes | Yes | 7d / 4w / 3m |
| `media/downloads` | No | No | No | No | No snapshots (transient data) |
| `s3/store` | No | Yes | Yes | No | 7d / 4w |
| `dev/gameservers` | No | Yes | Yes | No | 7d / 4w |
| `dev/vmdisks` | No | Yes | No | No | 7d |

Snapshots are local to the NAS. For off-site backup, TrueNAS supports
ZFS replication to a remote TrueNAS instance or cloud (e.g. Backblaze B2
via rclone). Out of scope for initial setup but worth adding later.

---

## Scrubs

ZFS scrubs verify data integrity against checksums. Schedule a monthly
scrub on all pools in TrueNAS. Scrubs on spinning rust take 12–24 hours
for large pools but run at low priority and do not impact normal I/O
meaningfully.

---

## Adding drives (operational procedure)

When you're ready to expand `media` pool with a new pair of drives:

1. Install drives in the NAS chassis
2. TrueNAS → Storage → `media` pool → Add VDEV
3. Select "Mirror" topology, select the two new drives
4. Confirm — pool expands immediately
5. Verify: TrueNAS pool status should show the new vdev and increased size
6. No service restarts required. NFS clients see the expanded space
   transparently.

Update `docs/homelab-hardware.md` with the new drive count and purchase date
when you do this.

---

## Existing drives (currently idle)

Two drives are waiting for the JBOD server. **Do not install them in the
current tiny server** (`serverhub`). Rationale:

- TrueNAS requires direct HBA access (IT mode) — no RAID controller.
  Installing into serverhub (which uses software RAID or a RAID controller)
  would require reformatting, losing existing data or requiring a migration
  dance.
- You'd be migrating data twice: serverhub → NAS, then rebuilding pool
  layout anyway when the proper hardware arrives.
- The correct moment is: NAS hardware assembled → TrueNAS installed → create
  pool with all available drives at once → rsync media from serverhub → done.

---

## Decisions deferred

| Decision | Options | Notes |
|---|---|---|
| Off-site backup | Backblaze B2 + rclone replication / second TrueNAS at a friend's place / none | Not blocking initial setup |
| SMB shares | Not configured initially — NFS covers all Linux VM use cases | Add SMB later if Windows/macOS client access is needed |
