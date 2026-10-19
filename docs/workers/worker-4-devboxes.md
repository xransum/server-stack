# Worker 4 — Devboxes

## Status
⏳ pending

## Trigger
Independent — can be started anytime. Does not depend on other workers.

---

## Opening Prompt (paste this verbatim to start the session)

```
You are Worker 4. Your only job is creating a devbox template VM on Proxmox
and verifying the clone workflow. Do not do anything outside this scope.
Read this file for all context you need.
When your job is complete, update the ## Status section of this file to "✅ completed",
add a ## Completion Notes section describing what was done and any decisions that shifted,
commit this file, push it, then ask me to confirm everything looks good before closing.

SECURITY: NEVER commit API keys, tokens, passwords, or webhook URLs to the repo. Use placeholders only. If you accidentally do, stop immediately and tell the user before pushing.

Context file: docs/workers/worker-4-devboxes.md in the xransum/server-stack repo.
```

---

## Infrastructure

- **Proxmox**: 192.168.1.168, `ssh proxmox` (homelab key `~/.ssh/homelab`, root user)
- **Proxmox storage**: LVM-thin pool named `data` on Samsung 990 Pro 2TB — ~1.9TB available
- **NAS**: 192.168.1.164, `ssh truenas` (homelab key, truenas_admin)
- **Repo**: `https://github.com/xransum/server-stack`
- **homelab pubkey**: `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ2qXOfPmNuK8Pp/YNCPgWcpWHqh0pltJEh3tI9r7EDH homelab`

## Devbox Concept

- **devbox-template**: base VM, never run directly, converted to Proxmox template
- **devbox-N**: thin-cloned on demand from template
- **Purpose**: coding from phone (NeoServer app) and laptop, on LAN and over Tailscale
- **Access**: SSH over Tailscale from anywhere

## devbox-template Specs

- **OS**: Ubuntu 24.04 LTS (consistent with other VMs)
- **vCPU**: 4 cores
- **RAM**: 8GB
- **Disk**: 32GB thin-provisioned on `data` pool
- **No GPU**

---

## Job Steps

### Step 1 — Create devbox-template VM on Proxmox

Create VM with above specs. Install Ubuntu 24.04 LTS.

### Step 2 — Base configuration

```bash
# Install base packages
sudo apt update && sudo apt install -y \
  git curl vim tmux htop build-essential \
  python3 python3-pip nodejs npm \
  zsh fzf ripgrep fd-find bat

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
```

### Step 3 — SSH hardening

```bash
mkdir -p ~/.ssh
echo "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJ2qXOfPmNuK8Pp/YNCPgWcpWHqh0pltJEh3tI9r7EDH homelab" >> ~/.ssh/authorized_keys
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo sed -i 's/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

### Step 4 — Clean up and snapshot

```bash
# Clean up before converting to template
sudo apt autoremove -y && sudo apt clean
sudo cloud-init clean 2>/dev/null || true
sudo truncate -s 0 /etc/machine-id
sudo rm -f /var/lib/dbus/machine-id
```

Snapshot in Proxmox:
- VM → Snapshots → Take Snapshot → name: `clean-base`

### Step 5 — Convert to template

In Proxmox UI: right-click the VM → **Convert to Template**

Or via CLI:
```bash
qm template <vmid>
```

### Step 6 — Test clone workflow

```bash
# Clone template to a test devbox
qm clone <template-vmid> <new-vmid> --name devbox-test --full 0

# Start it
qm start <new-vmid>

# SSH in (wait for it to get an IP)
# Run: sudo tailscale up → auth → verify SSH works over Tailscale
```

### Step 7 — Add Windows SSH config entry

Add to `~/.ssh/config` on Windows:
```
Host devbox-test
    HostName <LAN IP>
    User <username>
    IdentityFile ~/.ssh/homelab
```

### Step 8 — Test archive workflow

```bash
# Snapshot before archiving
qm snapshot <new-vmid> final-state

# Archive to NAS (optional, tests the full lifecycle)
vzdump <new-vmid> --dumpdir /mnt/dev/backups/ --compress zstd

# Destroy test devbox
qm destroy <new-vmid>
```

### Step 9 — Update repo

Commit any config changes. Update `docs/session-log.md` with what was done.

---

## Success Criteria

- [ ] devbox-template exists in Proxmox as a template (not a running VM)
- [ ] Template has: Ubuntu 24.04, base packages, Tailscale installed, SSH key auth
- [ ] Successfully cloned a test devbox from template
- [ ] SSH into test devbox works via homelab key
- [ ] Tailscale auth works on cloned devbox
- [ ] Test devbox destroyed after verification

---

## Out of Scope

- Do NOT touch media-vm or any running services
- Do NOT set up game servers (Worker 3)
- Do NOT decommission serverhub (Worker 2)
- Do NOT configure specific dev environments or dotfiles — template is base OS only

---

## Completion Notes

(fill in when done)
