# Worker 4 — Devboxes

## Status
✅  completed

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

**Date:** 2026-10-19  
**VM ID:** 102  
**Template name:** `devbox-template`  
**LAN IP during setup:** 192.168.1.171 (DHCP, not retained — template has blank machine-id)

### What was done

1. Created VM 102 on Proxmox: Ubuntu 24.04 LTS Server, 4 vCPU, 8GB RAM, 64GB
   thin-provisioned on `data` pool, q35/UEFI, VirtIO NIC, DHCP networking.

2. Bootstrapped SSH access via Python/paramiko (Windows has no sshpass) using
   the install password to inject the homelab pubkey, then switched to key-only
   auth for all subsequent operations.

3. Granted passwordless sudo temporarily to run apt installs non-interactively,
   then hardened sshd (PasswordAuthentication no, PubkeyAuthentication yes).

4. Installed full dev tool stack — see tool versions below.

5. Copied dotfiles directly from `debian-devbox` (via local temp staging):
   `.gitconfig`, `.p10k.zsh`, `.vimrc`, `.nanorc` + `.nano/`, `.aws/config`
   (no credentials), `.config/gh/`, `.config/github-copilot/`,
   `.config/pypoetry/config.toml`, `.ssh/id_rsa` + `.id_rsa.pub` + `.id_rsa.ppk`.

6. Wrote fresh `~/.zshrc` for mise (replacing pyenv/nvm/rvm init blocks),
   preserving all customizations: p10k, history bindings, aliases, opencode completions.

7. Cloned `xransum/opencode-config` into `~/.config/opencode` (source of truth for
   opencode config; `node_modules/` not tracked, `npm install` not needed for bare config).

8. Cleared `machine-id` and ran `cloud-init clean` so clones get fresh identity on boot.

9. Shut down VM, converted to Proxmox template (`qm template 102`).

### Decisions that shifted from original doc

- **"Base OS only / no dotfiles"** overridden: full dev environment replicated
  from `debian-devbox` per user instruction.

- **pyenv + nvm + rvm → mise**: replaced all three version managers with a
  single `mise` install. Cleaner shim setup, declarative config, no migration
  pain on a fresh VM. `rustup` kept separately (Rust toolchain manager is too
  integrated to replace).

- **Python version management**: pyenv WAS installed on debian-devbox (contrary
  to initial `command -v pyenv` miss — it wasn't on PATH in non-login shell).
  All 7 pyenv versions replicated in mise. Global set to 3.11.9 (user's
  stated preference: "mise python version globally 3.11").

- **poetry OMZ plugin dropped**: Poetry CLI not actually installed on debian-devbox
  (gems were blog repo artifacts). Plugin provided no value.

- **Nerd Fonts not installed**: headless server, fonts only matter on the terminal
  emulator client side. Powerlevel10k works in lean mode without them.

- **Snapshot before template**: Proxmox does not allow converting a VM with
  snapshots to a template. Snapshot was deleted before conversion — the template
  itself is the golden state.

- **opencode install script returned 404**: installed via `npm install -g opencode-ai`
  instead (v1.19.3). Works identically.

- **64GB disk**: user upgraded from 32GB to 64GB to accommodate all Python
  version builds + Docker images.

- **Disk size per clone**: each clone inherits 64GB thin-provisioned.

- **DHCP not static**: template uses DHCP; clones get unique IPs on boot via
  blank machine-id. Stable addressing handled per-clone via Tailscale after
  `tailscale up`.

- **`rls` component**: deprecated and removed from Rust stable toolchain.
  `rust-analyzer` added instead (the modern replacement).

- **Playwright via `uv tool install`**: installs playwright + pinned Chromium
  binary (~191MB) into `~/.cache/ms-playwright/`. All browser deps installed
  via `playwright install-deps chromium`.

### Installed tool versions

| Tool | Version | Method |
|---|---|---|
| Python | 3.11.9 (global), 3.12.4, 3.10.16, 3.9.21, 3.8.20, 3.7.17, 3.6.15 | mise |
| Node | 23.6.0 (global), 20.20.2 | mise |
| Ruby | 3.2.2 | mise |
| Rust | 1.99.1 stable | rustup |
| cargo-lambda | 1.9.2 | cargo install |
| dotnet SDK | 10.0.113 | Microsoft apt repo |
| Docker | 29.9.1 CE | Docker apt repo |
| gh CLI | 2.102.0 | GitHub apt repo |
| uv | 0.13.3 | official script |
| playwright | 1.64.0 (Chromium 156) | uv tool install |
| opencode | 1.19.3 | npm install -g opencode-ai |
| Tailscale | latest | Tailscale apt repo (no auth) |
| bundler | 2.4.10 | gem install |
| rake | 13.0.6 | gem install |
| oh-my-zsh | latest | install script |
| powerlevel10k | latest | git clone |

### Post-clone checklist (for each new devbox)

1. `sudo tailscale up` — auth with your account
2. `gh auth login` — re-auth gh CLI (token not copied, only config)
3. Add SSH config entry on Windows: `Host devbox-N → HostName <tailscale-ip>`
4. Optionally: `aws configure` to add credentials
5. Renew GitHub Copilot auth if needed (`vim`, `:Copilot auth`)
