# Worker 3 — Game Servers

## Status
⏳ pending

## Trigger
Independent — can be started anytime. Does not depend on other workers.

---

## Opening Prompt (paste this verbatim to start the session)

```
You are Worker 3. Your only job is setting up the gameservers-vm on Proxmox
and deploying Pelican Panel + Wings. Do not do anything outside this scope.
Read this file for all context you need.
When your job is complete, update the ## Status section of this file to "✅ completed",
add a ## Completion Notes section describing what was done and any decisions that shifted,
commit this file, push it, then ask me to confirm everything looks good before closing.

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs to the repo. Use placeholders only. If you accidentally do, stop immediately and tell the user before pushing.

Context file: docs/workers/worker-3-gameservers.md in the xransum/server-stack repo.
```

---

## Infrastructure

- **Proxmox**: 192.168.1.168, `ssh proxmox` (homelab key `~/.ssh/homelab`, root user)
- **media-vm**: 192.168.1.169, `ssh media-vm` (homelab key, ransum user) — already running, do not disturb
- **NAS**: 192.168.1.164, `ssh truenas` (homelab key, truenas_admin user)
- **Repo**: `https://github.com/xransum/server-stack` (clone at `/opt/media/server-stack` on media-vm)
- **homelab pubkey**: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ2qXOfPmNuK8Pp/YNCPgWcpWHqh0pltJEh3tI9r7EDH homelab`

## Proxmox Storage

- Boot disk: WD Green SN350 250GB (nvme0n1) — Proxmox OS
- VM storage: Samsung 990 Pro 2TB (nvme1n1) — LVM-thin pool named `data`, VG named `data`
- media-vm is VM ID 100 on this pool

## gameservers-vm Specs (from docs/proxmox-compute.md)

- **VM ID**: pick next available (101+)
- **Name**: `gameservers-vm`
- **OS**: Ubuntu 24.04 LTS
- **vCPU**: 8 cores (host type)
- **RAM**: 32GB, ballooning disabled
- **Disk**: 32GB thin-provisioned on `data` pool
- **Network**: VirtIO on vmbr0
- **Machine**: q35, BIOS: OVMF (UEFI)
- **No GPU**

## NAS Game Server Storage

- `/mnt/dev/gameservers/` — Pelican backup target (not live worlds)
- Live worlds run on gameservers-vm local disk via Wings
- NFS exports from NAS are scoped to `192.168.1.0/24` — gameservers-vm will be on that subnet

---

## Job Steps

### Step 1 — Create gameservers-vm on Proxmox

Create the VM via Proxmox UI or CLI with the specs above.
Install Ubuntu 24.04 LTS.

SSH hardening (same as all homelab VMs):
```bash
# On gameservers-vm as root:
mkdir -p ~/.ssh
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ2qXOfPmNuK8Pp/YNCPgWcpWHqh0pltJEh3tI9r7EDH homelab" >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
systemctl restart ssh
```

Add to Windows SSH config (`~/.ssh/config`):
```
Host gameservers-vm
    HostName <LAN IP>
    User <username>
    IdentityFile ~/.ssh/homelab
```

### Step 2 — Install Tailscale on gameservers-vm

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --accept-routes
```

Authenticate via the URL shown. Update SSH config with Tailscale IP:
```
Host gameservers-vm-ts
    HostName <tailscale IP>
    User <username>
    IdentityFile ~/.ssh/homelab
```

### Step 3 — Install Docker on gameservers-vm

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

### Step 4 — Clone repo and deploy Pelican

```bash
git clone https://github.com/xransum/server-stack.git /opt/gameservers/server-stack
cd /opt/gameservers/server-stack/compose
cp .env.example .env
# Edit .env: set PELICAN_APP_URL=https://pelican.xransum.com and PELICAN_DB_PASSWORD=<strong password>
docker compose -f docker-compose.gameservers.yml up -d
```

### Step 5 — Configure Pelican Panel

Access Panel at `http://<gameservers-vm-ip>:80` initially.
Create admin user:
```bash
docker compose -f docker-compose.gameservers.yml exec panel php artisan p:user:make
```

Register the Wings node in the Panel UI:
- Panel → Admin → Nodes → Create Node
- Copy the Wings config token
- On gameservers-vm: paste token into Wings config

### Step 6 — Add pelican.xransum.com to CF tunnel

The CF tunnel runs on media-vm. Add a proxy host in NPM:
- `pelican.xransum.com` → `http://<gameservers-vm-LAN-IP>:80`

Then add to tunnel ingress via Cloudflare API or dashboard.
Also add CNAME DNS record: `pelican.xransum.com` → tunnel CNAME (orange cloud).

### Step 7 — Add NFS mount for game backups (optional)

```bash
sudo mkdir -p /mnt/gameservers
# Add to /etc/fstab:
# 192.168.1.164:/mnt/dev/gameservers /mnt/gameservers nfs rw,hard,intr,_netdev,noatime 0 0
sudo mount -a
```

### Step 8 — Create first game server in Pelican

Use the Panel UI to create a game server from an egg.
Set RAM/CPU limits per game at creation time (no universal default).

### Step 9 — Port forwards (user does this on router)

Per game server, ask user to add a port forward on the router:
- mc1: `25565 TCP → gameservers-vm IP`
- mc2: `25566 TCP → gameservers-vm IP`
- valheim: `2456-2457 UDP → gameservers-vm IP`
- satisfactory: `7777 UDP → gameservers-vm IP`
- factorio: `34197 UDP → gameservers-vm IP`

cloudflare-ddns on media-vm already keeps all these subdomains updated with home IP.

### Step 10 — Migrate old game server data from serverhub (if applicable)

serverhub has `/home/minecraft/` and `/home/steam/` with world saves.
Copy these to the appropriate game server data dirs in Pelican/Wings.
Coordinate with user on which worlds to migrate.

### Step 11 — Update repo

Commit any config changes. Update `docs/session-log.md`.

---

## Success Criteria

- [ ] gameservers-vm created and accessible via SSH
- [ ] Tailscale installed and authed
- [ ] Docker running
- [ ] Pelican Panel accessible at pelican.xransum.com
- [ ] Wings node registered in Panel
- [ ] At least one game server created and running
- [ ] Port forward set up for that game server

---

## Out of Scope

- Do NOT touch media-vm Docker services
- Do NOT touch DNS records other than pelican.xransum.com
- Do NOT decommission serverhub (that's Worker 2)
- Do NOT set up devboxes (that's Worker 4)

---

## Completion Notes

(fill in when done)
