# Worker 1 — GPU Passthrough

## Status
✅ completed

## Trigger
RTX 2070 must be physically installed in the Proxmox machine before starting this session.
Serverhub can be powered off to remove the GPU, then powered back on if needed for reference.

---

## Opening Prompt (paste this verbatim to start the session)

```
You are Worker 1. Your only job is GPU passthrough for the homelab Proxmox server.
Do not do anything outside this scope. Read this file for all context you need.
When your job is complete, update the ## Status section of this file to "✅ completed",
add a ## Completion Notes section describing what was done and any decisions that shifted,
commit this file, push it, then ask me to confirm everything looks good before closing.

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs to the repo. Use placeholders only. If you accidentally do, stop immediately and tell the user before pushing.

Context file: docs/workers/worker-1-gpu.md in the xransum/server-stack repo.
```

---

## Infrastructure

- **Proxmox**: 192.168.1.168, `ssh proxmox` (homelab key `~/.ssh/homelab`, root user)
- **media-vm**: 192.168.1.169, `ssh media-vm` (homelab key, ransum user, passwordless sudo)
- **Proxmox version**: PVE 9.2.21
- **media-vm OS**: Ubuntu 24.04
- **Docker Compose**: `/opt/media/server-stack/compose/` on media-vm
- **Repo**: `https://github.com/xransum/server-stack` (clone at `/opt/media/server-stack`)

## GPU

- **Card**: NVIDIA RTX 2070
- **Current location**: serverhub (192.168.1.166) — physically move to Proxmox machine before starting
- **Purpose**: Plex NVENC hardware transcoding (requires Plex Pass — user has it)
- **IOMMU**: AMD IOMMU enabled in BIOS on Proxmox machine

---

## Job Steps

### Step 1 — Verify IOMMU and find GPU PCI address on Proxmox

```bash
# Verify IOMMU groups
find /sys/kernel/iommu_groups/ -type l | sort -V | head -20

# Find RTX 2070
lspci | grep -i nvidia
```

Note the PCI address (e.g. `01:00.0`).

### Step 2 — Add GPU to media-vm in Proxmox UI

Proxmox UI → media-vm (VM 100) → Hardware → Add → PCI Device
- Select the RTX 2070
- Enable: **All Functions**, **ROM-Bar**, **PCI-Express**
- Save → Reboot media-vm

### Step 3 — Install NVIDIA drivers inside media-vm

```bash
ssh media-vm
sudo apt install -y linux-headers-$(uname -r) nvidia-driver-550 nvidia-utils-550
# Reboot required
sudo reboot
```

After reboot:
```bash
nvidia-smi  # should show RTX 2070
```

### Step 4 — Install NVIDIA Container Toolkit

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

Verify:
```bash
docker run --rm --gpus all nvidia/cuda:12.0-base-ubuntu22.04 nvidia-smi
```

### Step 5 — Re-add GPU block to Plex in compose file

The GPU block was stripped from `docker-compose.yml` for initial deploy.
Add it back to the `plex` service in `/opt/media/server-stack/compose/docker-compose.yml`:

```yaml
    # NVIDIA GPU passthrough for NVENC hardware transcoding (Plex Pass required)
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

Then restart Plex:
```bash
cd /opt/media/server-stack/compose
docker compose --profile media up -d plex
```

### Step 6 — Enable hardware transcoding in Plex

Plex Web UI → Settings → Transcoder:
- Enable **Hardware-Accelerated Streaming**: ✅
- Verify transcoder shows NVENC in active sessions

### Step 7 — Commit compose change to repo

```bash
cd /opt/media/server-stack
git add compose/docker-compose.yml
git commit -m "compose: re-add NVIDIA GPU block to plex service"
git push
```

---

## Success Criteria

- [ ] `nvidia-smi` shows RTX 2070 inside media-vm
- [ ] `docker run --rm --gpus all nvidia/cuda:...` works
- [ ] Plex container starts without errors
- [ ] Plex Settings → Transcoder shows hardware acceleration available
- [ ] Test stream shows NVENC in Plex dashboard

---

## Out of Scope

- Do NOT touch serverhub services or data
- Do NOT modify any other Docker services
- Do NOT change DNS or networking
- Do NOT work on game servers or devboxes

---

## Completion Notes

- RTX 2070 physically moved from serverhub to Proxmox machine
- IOMMU confirmed active (AMD-Vi), GPU at PCI `01:00.0` in IOMMU group 15 (all 4 functions in same group)
- GPU added to media-vm (VM 100) via `qm set 100 --hostpci0 host=0000:01:00.0;0000:01:00.1;0000:01:00.2;0000:01:00.3,pcie=1,rombar=1`
- NVIDIA drivers installed: `nvidia-driver-550` pulled in `nvidia-driver-580` (580.178.04) on Ubuntu 24.04 — both installed, 580 is the active driver
- Secure Boot blocked the nvidia kernel module (`Key was rejected by service`) — fixed by:
  1. Setting `pre-enrolled-keys=0` on the EFI disk via `qm set`
  2. Entering OVMF BIOS via GRUB → UEFI Firmware Settings → Device Manager → Secure Boot Configuration → disabled Attempt Secure Boot
- NVIDIA Container Toolkit installed and configured for Docker runtime
- GPU block already present in `docker-compose.yml` — Plex restarted with GPU access confirmed (`nvidia-smi` inside Plex container works)
- Plex Settings → Transcoder: hardware acceleration enabled, hardware-accelerated video encoding enabled
- GRUB timeout set to 3 seconds (was 10 during debug), boot menu arg removed from VM config
