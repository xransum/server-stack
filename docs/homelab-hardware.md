# Homelab Hardware BOM

Bill of materials for the full homelab build. Shopping list with confirmed
prices and links where available.

---

## Status

| Node | Status | Notes |
|---|---|---|
| NAS | ORDER PLACED | All parts ordered May/Jun 2026 |
| Compute | ORDER PLACED | All parts ordered Oct 2026 — $2,956.78 total |
| Network | DEFERRED | Buy after apartment move-in, layout confirmed |

---

## NAS Node — ORDERED ($1,193.37 total)

| Part | Qty | Price | Notes |
|---|---|---|---|
| Fractal Design Define 7 XL (solid black) | 1 | $239.99 | Case, storage layout, 18 HDD max |
| ASRock B550M Pro4 | 1 | $84.99 | AM4, 6x SATA, PCIe x16 + x4 |
| AMD Ryzen 5 5600G | 1 | $184.59 | Integrated graphics, Wraith Stealth cooler included |
| PUSKILL 16GB DDR4-3200 (2x8GB) | 1 | $109.00 | Non-ECC, adequate for NAS workloads |
| LSI 9207-8i HBA compatible IT mode | 1 | $69.88 | 8 additional SATA ports, SFF-8087 to SATA cables included. Must be IT mode not IR mode. |
| Seasonic Focus GX-850W ATX 3.1 | 1 | $139.99 | 190mm length, fits storage layout PSU clearance |
| 10Gtek X550-AT2 10GbE NIC | 1 | $93.99 | Intel X550-AT2 chip, 10GBASE-T RJ45 |
| Noctua NF-F12 iPPC 3000 PWM | 3 | $89.85 | High static pressure, replaces stock fans — mandatory |
| Fractal HDD Tray Kit Type-B 2-pack | 5 | $86.70 | Expands case to 18 HDD bays |
| Fractal Universal Multibracket 2-pack | 1 | $15.00 | Additional drive mounting |
| Kingston A400 240GB SATA SSD | 1 | $78.75 | Boot drive |

### NAS drives (already owned)

| Drive | Size | Health | Role |
|---|---|---|---|
| Seagate IronWolf ST10000VN000 x2 | 10TB each | Clean, healthy, RAID1 on serverhub | Initial mirror VDEV |
| Seagate Barracuda ST3000DM001 | 3TB | FAILING — 32 uncorrectable errors, Load_Cycle_Count maxed | Retire immediately, do not migrate |
| HGST Travelstar HTS541010A9E680 | 1TB | 21,127 hours, 27 ICRC errors | Retire |
| SanDisk SDSSDH3512G | 512GB | Clean, healthy | Spare after serverhub decommission |

### NAS build notes

- Convert Define 7 XL to storage layout BEFORE installing any components.
  Strip to bare frame, reposition drive cages. PSU clearance drops to 227mm
  in storage layout — Seasonic Focus GX-850W is 190mm, confirmed safe.
- Fan replacement is mandatory. Stock 80mm fans are insufficient for packed
  storage. Replace before first boot.
- HBA must be IT mode (passthrough). IR mode (RAID) is incompatible with ZFS.
- Starting pool: 2x 10TB mirror. Add drives in pairs as budget allows.
  See `docs/storage.md` for expansion procedure.

---

## Compute Node — ORDERED ($2,956.78 total)

All parts ordered Oct 2026 via Amazon. PSU sourced from spare Seasonic GX-850W
(originally purchased for NAS, second unit used here — not in total above).

| Part | Qty | Price | Notes |
|---|---|---|---|
| Fractal Design Define 7 XL (solid black) | 1 | $239.99 | Same case as NAS |
| ASUS ProArt X870E-CREATOR WiFi | 1 | $506.99 | AM5, PCIe 5.0, 4x M.2, 10Gb onboard LAN |
| AMD Ryzen 9 7950X3D | 1 | $579.95 | 16c/32t, 5.7GHz boost, 3D V-Cache, AM5 |
| G.Skill Trident Z5 RGB 64GB DDR5-6000 CL36 (2x32GB) | 1 | $989.99 | White. DDR5-6000 CL36, better availability than original DDR5-6400 spec |
| Noctua NH-D15 G2 chromax.Black | 1 | $169.90 | AM5, dual-tower air cooler |
| Samsung 990 Pro 2TB NVMe | 1 | $389.99 | VM disk images |
| WD Green SN350 250GB NVMe | 1 | $79.97 | Proxmox OS boot drive |
| Seasonic Focus GX-850W ATX 3.1 | 1 | $0.00 | Spare unit from NAS order — not purchased separately |
| 10Gtek X550-AT2 10GbE NIC | 1 | $93.99 | Same NIC as NAS — still needed, not in Amazon order |

### Free from serverhub (pull after decommission)

| Part | Notes |
|---|---|
| NVIDIA RTX 2070 | PCIe passthrough to media-vm for Plex NVENC transcoding |
| SanDisk SDSSDH3512G 512GB SSD | Spare/extra storage |

---

## Network — DEFERRED

Buy after apartment move-in once room layout is confirmed.

| Part | Est. Price | Notes |
|---|---|---|
| TP-Link TL-SX1008 (8x 10GbE RJ45 unmanaged) | ~$150-200 | Dumb 10GbE switch. NAS and compute connect here. FIOS gateway retained for routing. |

FIOS gateway is kept as-is for internet routing. No managed switch, no
MikroTik router. The TP-Link TL-SX1008 is unmanaged — zero config, plug
and play, both servers connect at 10GbE.

---

## Running costs

**Effective electricity rate:** $0.361/kWh all-in (verified from May 2026
Dominion Energy bill: $99.68 / 276 kWh).

The existing bill already covers: gaming PC, current media server (2-3 drives),
2x TVs, 3x monitors, normal apartment usage.

| Scenario | Net added draw | Added monthly cost | Est. total bill |
|---|---|---|---|
| Phase 1: NAS only | ~80-100W | ~$21-26 | ~$121-126 |
| Phase 1+2: NAS + compute | ~100-130W | ~$26-34 | ~$126-134 |
| Full build with 10GbE switch | ~140-180W | ~$37-47 | ~$137-147 |
