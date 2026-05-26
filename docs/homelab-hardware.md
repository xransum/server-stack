# Homelab Hardware

Hardware bill of materials, shopping list, and build notes for the
two-server homelab (NAS + Proxmox compute). See `docs/storage.md` and
`docs/proxmox-compute.md` for the architecture decisions behind these
hardware choices.

> **Buy timing:** Wait until you have the physical space (apartment) before
> purchasing rack and NAS chassis. Confirm room dimensions first.
> RAM prices are volatile through mid-2026 (AI DRAM shortage). Check
> current pricing weekly before ordering — numbers below are April 2026
> approximations.

---

## Architecture overview

```
Internet (FIOS)
    |
    | Ethernet WAN handoff (request from FIOS, no Verizon router needed)
    |
MikroTik RB4011 Router
    |
MikroTik CRS317 Switch (10GbE SFP+)
    |-------------------+
    |                   |
NAS Node           Compute Node
(TrueNAS Scale)    (Proxmox VE)
4U Rackmount       Tower or rack
ZFS mirror pools   media-vm + gameservers-vm
NFS server         Docker Compose stacks
```

10GbE between NAS and compute (~500–800 MB/s sustained) makes NFS
transparent — services inside media-vm treat NAS storage as local disk.

---

## Node: NAS

### Role

Persistent bulk storage. ZFS pools. NFS server. Always on, never strained.
No application logic runs here — only TrueNAS and ZFS.

### Pool layout (see `docs/storage.md` for full rationale)

| Pool | Drives | Topology | Usable |
|---|---|---|---|
| `media` | 2× 12TB (start), expand in pairs | ZFS mirror vdevs | 12TB → 96TB+ |
| `s3` | 2× 12TB (start), expand in pairs | ZFS mirror vdevs | 12TB+ |
| `dev` | 2× 2TB | ZFS mirror | 2TB |

**Why mirror vdevs (not RAIDZ):** RAIDZ vdev shape is fixed at creation.
Mirrors allow adding pairs of drives without rebuilding. `zpool add media
mirror <dev1> <dev2>` expands the pool instantly with no downtime and no
path changes. See `docs/storage.md`.

### NAS hardware

| Component | Part | Notes |
|---|---|---|
| Chassis | Norco RPC-4224 | 4U, 24× hot-swap 3.5" SATA/SAS bays |
| Motherboard | Supermicro X10SRi-F (used) | LGA2011-3, ECC DDR4, IPMI, eBay only |
| CPU | Intel Xeon E5-1650 v4 (used) | Low TDP, adequate for NAS workloads |
| RAM | 32 GB DDR4 ECC RDIMM (4× 8 GB) | Server-grade ECC required for ZFS |
| HBA | LSI 9211-8i IT mode (used) | Must be IT mode — ZFS requires passthrough |
| SAS Expander | Intel RES2SV240 (used) | Routes all 24 bays through single HBA |
| Boot | 1–2× SSD/NVMe (32 GB+) | Do NOT use USB flash — fails under constant writes in 1–2 years |
| Data (start) | 4× 12TB NAS drives (2 media, 2 s3) | Seagate IronWolf Pro or WD Gold |
| PSU | Corsair RM1000x 1000W | Headroom for 24 drives at full load |
| Fans (mod) | 3× Noctua NF-F12 PWM + 2× Noctua NF-A8 PWM | Replace stock 80mm ball-bearing fans before first boot |

### NAS fan mod (not optional)

The Norco RPC-4224 ships with loud 80mm ball-bearing fans. Replace before
first boot:

- Drive bay wall divider: replace 4× 80mm fans with 3× Noctua NF-F12 PWM
  (120mm, high static pressure, fits with adapter brackets)
- Rear exhaust: replace 2× 80mm fans with 2× Noctua NF-A8 PWM

This drops noise ~12–15 dB and keeps drives within spec temp range.
The TrueNAS community has documented this extensively.

Note: 24 spinning drives produce low-frequency motor hum regardless of
fan choice. Put the rack in a corner or closet with ventilation, not in
a bedroom.

---

## Node: Compute

### Role

Runs everything via Proxmox VMs. Reads media from NAS over NFS.
Active game server installs and devbox VM disks live on local NVMe.

### Compute hardware

| Component | Part | Notes |
|---|---|---|
| Case | Fractal Design Define 7 XL | Best-in-class acoustic enclosure |
| Motherboard | ASUS Pro WS X570-ACE | ECC support, solid VRM, good PCIe layout for GPU PT |
| CPU | AMD Ryzen 9 5950X (used/open box) | 16c/32t, 4.9 GHz boost, excellent single-thread, AM4 |
| RAM | 128 GB DDR4 3200 MHz (4× 32 GB) | See DDR4 note below |
| Primary NVMe | Samsung 990 Pro 2 TB × 2 | ZFS mirror — Proxmox OS + VM images |
| Scratch NVMe | WD Black SN850X 2 TB | Active game server installs, ephemeral dev scratch |
| 10GbE NIC | Intel X550-T1 | 10GBASE-T single port, connects to CRS317 |
| GPU | NVIDIA 2070 (moved from serverhub) | PCIe passthrough to media-vm for Plex NVENC |
| PSU | Seasonic Prime TX-1000 | Quiet fan curve, long warranty |
| Fans | Noctua NF-A14 PWM × 4 | Replace Define 7 XL stock fans |

### Why DDR4 and not DDR5

DDR5 128 GB kits run $1,400–1,700 in 2026 due to the AI DRAM shortage.
DDR4 128 GB (4× 32 GB) runs $300–350. The 5950X is AM4 — DDR4 native.
Revisit DDR5 on AM5 when prices normalize in late 2026/2027.

### VM layout summary (see `docs/proxmox-compute.md` for full detail)

| VM | vCPU | RAM | GPU | Purpose |
|---|---|---|---|---|
| `media-vm` | 8–12 | 16–32 GB | NVIDIA 2070 PT | Full media stack (Docker Compose) |
| `gameservers-vm` | 8–12 | 16–32 GB | None | Game servers (Docker Compose) |
| `devbox-template` | 4 | 8 GB | None | Template for linked clones |

---

## Network

| Component | Part | Notes |
|---|---|---|
| Router | MikroTik RB4011iGS+RM | 1U rackmount, WireGuard built-in, full RouterOS |
| Switch | MikroTik CRS317-1G-16S+RM | 1U, 16× SFP+ 10GbE, passive cooling |
| DAC cables | SFP+ DAC 1m × 3 | NAS → switch, compute → switch, spare |

### FIOS Ethernet WAN handoff

Request an Ethernet WAN handoff from the FIOS ONT. This gives a plain
Ethernet cable from the wall into the MikroTik WAN port — no Verizon
router in the loop. Call FIOS and ask for "Ethernet WAN handoff." If they
push back, enable IP Passthrough on their router instead (less clean but
functional).

### RouterOS DDNS (optional, replaces dns-updater)

Once MikroTik is deployed, DDNS can fire on IP-change events via a
RouterOS script instead of a cron on the Proxmox host. In practice,
Cloudflare Tunnel (the `cloudflared` Docker service) removes the need
for DDNS entirely — no public IP needed for the tunnel.

---

## Rack

| Item | Notes |
|---|---|
| NavePoint 18U 4-Post Open Frame with Casters | Holds NAS (4U) + switch (1U) + router (1U) = 6U used, 12U free |
| Anti-vibration rubber mat | Put under rack feet — 24 spinning drives transmit vibration through floors to downstairs neighbors |
| APC Smart-UPS 1500VA | **Non-optional.** Sudden power cut during ZFS write = pool corruption. Buy with or before the NAS. |

### Rack layout (18U)

```
1U  - MikroTik RB4011 router
1U  - MikroTik CRS317 switch
1U  - Patch panel (optional)
1U  - Blanking panel
4U  - Norco RPC-4224 NAS
1U  - Horizontal PDU / power strip
---
9U used, 9U free for future expansion
```

Compute tower sits beside the rack on the floor (no rails needed).

---

## Shopping list

> Prices are April 2026 approximations. Verify before ordering.
> Amazon links are search links — compare with eBay and Microcenter before buying.

### Rack

| Item | Source | Approx price |
|---|---|---|
| NavePoint 18U 4-Post Open Frame with Casters | Amazon | ~$312 |
| Anti-vibration rubber mat | Amazon | ~$20 |
| APC Smart-UPS 1500VA | Amazon / Microcenter Fairfax | ~$400 |

### NAS

| Item | Source | Approx price |
|---|---|---|
| Norco RPC-4224 chassis | Amazon (check for restock) / eBay | ~$350–450 |
| Supermicro X10SRi-F motherboard | eBay only (98%+ feedback, "tested working") | ~$80–150 |
| Intel Xeon E5-1650 v4 | Amazon / eBay | ~$30–60 |
| 32 GB DDR4 ECC RDIMM 4× 8 GB | Amazon (server-grade, not consumer DDR4) | ~$60–100 |
| LSI 9211-8i IT mode HBA | Amazon / eBay — **confirm IT mode before buying** | ~$30–60 |
| Intel RES2SV240 SAS Expander | Amazon / eBay | ~$30–60 |
| 32 GB+ SSD for boot | Amazon / Microcenter Fairfax | ~$25 |
| Seagate IronWolf Pro 12TB × 4 (starter) | Amazon | ~$200–240 each |
| Corsair RM1000x 1000W PSU | Amazon / Microcenter Fairfax | ~$150 |
| Noctua NF-F12 PWM × 3 | Amazon / Microcenter Fairfax | ~$25 each |
| Noctua NF-A8 PWM × 2 | Amazon / Microcenter Fairfax | ~$20 each |

### Compute

| Item | Source | Approx price |
|---|---|---|
| Fractal Design Define 7 XL | Amazon / Microcenter Fairfax | ~$200 |
| ASUS Pro WS X570-ACE | Amazon / Microcenter Fairfax | ~$250–300 |
| AMD Ryzen 9 5950X (used/open box) | Microcenter Fairfax open box / Amazon | ~$200–250 |
| 32 GB DDR4 3200 × 4 (128 GB total) | Amazon / Microcenter Fairfax — check weekly | ~$300–350 |
| Samsung 990 Pro 2 TB × 2 | Amazon / Microcenter Fairfax | ~$150 each |
| WD Black SN850X 2 TB | Amazon / Microcenter Fairfax | ~$150 |
| Intel X550-T1 10GbE NIC | Amazon | ~$80–120 |
| NVIDIA 2070 | Moving from serverhub — no purchase needed | — |
| Seasonic Prime TX-1000 PSU | Amazon / Microcenter Fairfax | ~$200 |
| Noctua NF-A14 PWM × 4 | Amazon / Microcenter Fairfax | ~$25 each |

### Network

| Item | Source | Approx price |
|---|---|---|
| MikroTik CRS317-1G-16S+RM | Amazon | ~$300 |
| MikroTik RB4011iGS+RM | Amazon | ~$200 |
| SFP+ DAC cable 1m × 3 | Amazon | ~$10–15 each |

---

## Microcenter Fairfax notes

Good for: CPU, RAM, NVMe, PSU, case, GPU, fans, some network gear.
Does not carry: Supermicro boards, LSI HBAs, SAS expanders, MikroTik.
Call ahead or check website for open box CPU and GPU availability before
making the trip.

---

## Gotchas and non-obvious requirements

**LSI 9211-8i must be IT mode (not IR mode).**
TrueNAS and ZFS require HBA passthrough (IT mode). IR mode is hardware
RAID and will not work correctly with ZFS. Many eBay listings state the
mode explicitly — confirm before purchasing. If unlisted, ask the seller.

**Supermicro X10SRi-F is eBay-only.**
Not carried by Microcenter or typically available new on Amazon. Buy from
sellers with 98%+ feedback and "tested working" explicitly in the listing.

**Norco RPC-4224 is frequently out of stock.**
Watch the Amazon listing for restocks. eBay is a reliable alternative
but usually 20–30% more expensive.

**UPS before first NAS power-on.**
ZFS uses copy-on-write but an unclean shutdown during a transaction can
corrupt the pool. The APC Smart-UPS 1500VA covers the rack + NAS at
full load (~360W) for ~15–20 minutes. Enough for a controlled shutdown
during a power outage.

**RAM prices in 2026.**
The AI DRAM shortage has pushed DDR5 to 3–5× 2024 prices. DDR4 is more
stable but also rising. Check prices weekly rather than assuming the
numbers above are current. Prices are projected to normalize in late
2026/2027.

---

## Phased build order

### Phase 1 — NAS first (recommended)

Build the NAS, set up TrueNAS, create ZFS pools, configure NFS. Then
rsync media from serverhub to NAS. This gets the data off the single-
point-of-failure tiny server immediately, even before the compute node
exists.

### Phase 2 — Compute node

Build the Proxmox tower. Create media-vm and gameservers-vm. Mount NFS
shares. Deploy Docker Compose stack. Migrate config from serverhub. See
`docs/migration.md` for the full step-by-step.

### Phase 3 — Network

Deploy MikroTik router and CRS317 switch. Request FIOS Ethernet WAN
handoff. Configure VLANs and firewall rules. 10GbE everything. WireGuard
on RB4011 as optional self-hosted VPN enhancement (Tailscale stays
primary). Retire `dns-updater` in favor of CF Tunnel.

---

## Running costs

Dominion Energy (Northern Virginia) runs ~$0.12–0.13/kWh.
Prices below are estimates — actual draw depends on drive count and load.

| Configuration | Avg draw | Monthly kWh | Monthly cost |
|---|---|---|---|
| NAS (4 drives, idle) | ~120 W | ~87 kWh | ~$11 |
| NAS (24 drives, idle) | ~280 W | ~202 kWh | ~$24–26 |
| Compute node (idle) | ~120 W | ~87 kWh | ~$11 |
| Full lab (NAS 24d + compute, idle) | ~400 W | ~290 kWh | ~$35–38 |

Other ongoing costs:

| Item | Cost |
|---|---|
| FIOS Gigabit internet | ~$60–80/month |
| Cloudflare DNS + Tunnel | Free (personal use) |
| Tailscale personal tier | Free (up to 100 devices) |
| Domain name | ~$10–15/year |
| Proxmox VE | Free (community repos) |
| TrueNAS Scale | Free |
| Plex Pass | $5/month or $120 lifetime (required for NVENC hardware transcoding) |
