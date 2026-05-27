#!/bin/bash
# ============================================================
# ZeroChainAI VPS Security Hardening Script
# Run as root on fresh Debian/Ubuntu server
# Usage: bash setup_vps.sh <zeroscan_user_password>
# ============================================================
set -euo pipefail
LOG="/var/log/zeroscan_setup.log"
exec > >(tee -a "$LOG") 2>&1

echo "═══════════════════════════════════════════════════"
echo "  ZeroChainAI VPS Hardening — $(date -u)"
echo "═══════════════════════════════════════════════════"

# ── 0. Prerequisites ────────────────────────────────────────
[[ $EUID -ne 0 ]] && { echo "Run as root"; exit 1; }
apt-get update -qq && apt-get upgrade -y -qq

# ── 1. Create non-root user ─────────────────────────────────
echo "[1/10] Creating zeroscan user..."
if ! id zeroscan &>/dev/null; then
    useradd -m -s /bin/bash -G sudo zeroscan
    echo "zeroscan:${1:-$(openssl rand -base64 32)}" | chpasswd
fi
mkdir -p /home/zeroscan/.ssh
chmod 700 /home/zeroscan/.ssh
chown -R zeroscan:zeroscan /home/zeroscan/.ssh
echo "✅ User zeroscan created"

# ── 2. SSH Hardening ────────────────────────────────────────
echo "[2/10] Hardening SSH..."
SSH_PORT=48114
cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak.$(date +%s)
cat > /etc/ssh/sshd_config <<'SSHCFG'
Port 48114
Protocol 2
HostKey /etc/ssh/ssh_host_ed25519_key
HostKey /etc/ssh/ssh_host_rsa_key

# Authentication
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
AuthorizedKeysFile .ssh/authorized_keys
AuthenticationMethods publickey
MaxAuthTries 3
LoginGraceTime 30
MaxSessions 5

# Security
X11Forwarding no
AllowTcpForwarding no
GatewayPorts no
PermitTunnel no
AllowAgentForwarding no
PrintMotd no
PermitEmptyPasswords no

# Only allow our user
AllowUsers zeroscan

# Crypto (only modern algorithms)
KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org
Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com
MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com
HostKeyAlgorithms ssh-ed25519,rsa-sha2-512

# Rate limiting
MaxStartups 5:30:10
SSHCFG
systemctl restart sshd
echo "✅ SSH hardened (port $SSH_PORT, key-only, no root)"

# ── 3. UFW Firewall ─────────────────────────────────────────
echo "[3/10] Configuring UFW firewall..."
apt-get install -y -qq ufw
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw deny in on any to any port 22      # block default SSH
ufw allow ${SSH_PORT}/tcp comment "SSH custom port"
ufw allow 51820/udp comment "WireGuard VPN"
# API port only accessible from WireGuard subnet (applied after wg0 up)
ufw allow in on wg0 to any port 8443 proto tcp comment "ZeroScan API via VPN only"
ufw --force enable
echo "✅ UFW: SSH($SSH_PORT) + WireGuard(51820) only"

# ── 4. WireGuard ────────────────────────────────────────────
echo "[4/10] Installing WireGuard..."
apt-get install -y -qq wireguard wireguard-tools
# Generate VPS keypair
wg genkey | tee /etc/wireguard/vps_private.key | wg pubkey > /etc/wireguard/vps_public.key
chmod 600 /etc/wireguard/vps_private.key
VPS_PRIVATE=$(cat /etc/wireguard/vps_private.key)
VPS_PUBLIC=$(cat /etc/wireguard/vps_public.key)

cat > /etc/wireguard/wg0.conf <<WGCFG
[Interface]
PrivateKey = ${VPS_PRIVATE}
Address = 10.99.0.1/24
ListenPort = 51820
PostUp   = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o eth0 -j MASQUERADE

# Mac Client — add after generating Mac keys
# [Peer]
# PublicKey = <MAC_PUBLIC_KEY>
# AllowedIPs = 10.99.0.2/32
WGCFG

chmod 600 /etc/wireguard/wg0.conf
systemctl enable wg-quick@wg0
echo "✅ WireGuard installed. VPS Public Key: $VPS_PUBLIC"
echo "   → Run setup_wireguard_mac.sh on your Mac, then add Mac pubkey to /etc/wireguard/wg0.conf"

# ── 5. fail2ban ─────────────────────────────────────────────
echo "[5/10] Configuring fail2ban..."
apt-get install -y -qq fail2ban
cat > /etc/fail2ban/jail.local <<'F2B'
[DEFAULT]
bantime  = 3600
findtime = 600
maxretry = 5
backend  = systemd

[sshd]
enabled  = true
port     = 48114
maxretry = 3
bantime  = 86400

[zeroscan-api]
enabled  = true
port     = 8443
logpath  = /var/log/zeroscan/access.log
maxretry = 20
bantime  = 86400
filter   = zeroscan-auth
F2B

cat > /etc/fail2ban/filter.d/zeroscan-auth.conf <<'FILTER'
[Definition]
failregex = ^.*"ip": "<HOST>".*"status": 40[13].*$
ignoreregex =
FILTER

systemctl enable fail2ban && systemctl restart fail2ban
echo "✅ fail2ban: SSH (3 попытки → 24ч бан)"

# ── 6. CrowdSec (DDoS + AI anomaly detection) ───────────────
echo "[6/10] Installing CrowdSec..."
curl -s https://packagecloud.io/install/repositories/crowdsec/crowdsec/script.deb.sh | bash
apt-get install -y -qq crowdsec crowdsec-firewall-bouncer-iptables
cscli collections install crowdsecurity/linux
cscli collections install crowdsecurity/sshd
cscli collections install crowdsecurity/http-generic
systemctl enable crowdsec && systemctl start crowdsec
echo "✅ CrowdSec installed with Linux + SSH + HTTP collections"

# ── 7. Kernel Hardening (sysctl) ────────────────────────────
echo "[7/10] Kernel hardening..."
cat > /etc/sysctl.d/99-zeroscan.conf <<'SYSCTL'
# Network security
net.ipv4.tcp_syncookies = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.conf.all.rp_filter = 1
net.ipv4.conf.default.rp_filter = 1
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
net.ipv4.conf.all.send_redirects = 0
net.ipv4.tcp_max_syn_backlog = 2048
net.ipv4.tcp_fin_timeout = 15
net.ipv4.tcp_keepalive_time = 300
net.ipv4.icmp_ignore_bogus_error_responses = 1

# IPv4 forwarding for WireGuard
net.ipv4.ip_forward = 1

# Memory protection
kernel.randomize_va_space = 2
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
kernel.dmesg_restrict = 1
kernel.kptr_restrict = 2

# Core dumps disabled
fs.suid_dumpable = 0
SYSCTL
sysctl -p /etc/sysctl.d/99-zeroscan.conf
echo "✅ Kernel hardened (SYN cookies, ASLR, no redirects)"

# ── 8. auditd ───────────────────────────────────────────────
echo "[8/10] Setting up auditd..."
apt-get install -y -qq auditd audispd-plugins
cat > /etc/audit/rules.d/zeroscan.rules <<'AUDIT'
# Monitor critical files
-w /etc/wireguard/ -p rwa -k vpn_config
-w /home/zeroscan/.ssh/ -p rwa -k ssh_access
-w /opt/vault/ -p rwa -k vault_data
-w /home/zeroscan/orchestrator/.env -p rwa -k env_access
# Monitor privilege escalation
-a always,exit -F arch=b64 -S setuid -S setgid -k priv_esc
-a always,exit -F arch=b64 -S execve -F euid=0 -k root_exec
# Monitor network connections
-a always,exit -F arch=b64 -S connect -k network_connect
AUDIT
systemctl enable auditd && systemctl restart auditd
echo "✅ auditd: monitoring VPN, SSH, Vault, privilege escalation"

# ── 9. Automatic Security Updates ───────────────────────────
echo "[9/10] Auto security updates..."
apt-get install -y -qq unattended-upgrades apt-listchanges
cat > /etc/apt/apt.conf.d/50unattended-upgrades <<'APT'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::AutoFixInterruptedDpkg "true";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
Unattended-Upgrade::Automatic-Reboot "false";
APT
echo 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' > /etc/apt/apt.conf.d/20auto-upgrades
echo "✅ Auto security updates enabled"

# ── 10. Docker ──────────────────────────────────────────────
echo "[10/10] Installing Docker..."
curl -fsSL https://get.docker.com | sh
usermod -aG docker zeroscan
# Docker daemon security
cat > /etc/docker/daemon.json <<'DOCKER'
{
  "icc": false,
  "no-new-privileges": true,
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "userns-remap": "default"
}
DOCKER
systemctl restart docker
echo "✅ Docker installed (user namespace remapping, no ICC)"

# ── Summary ─────────────────────────────────────────────────
VPS_IP=$(curl -s ifconfig.me 2>/dev/null || echo "unknown")
echo ""
echo "═══════════════════════════════════════════════════"
echo "  ✅ VPS HARDENING COMPLETE"
echo "═══════════════════════════════════════════════════"
echo "  VPS IP:         $VPS_IP"
echo "  SSH Port:       $SSH_PORT"
echo "  WireGuard Port: 51820 (UDP)"
echo "  VPS WG Pubkey:  $VPS_PUBLIC"
echo ""
echo "  NEXT STEPS:"
echo "  1. Add your SSH public key:"
echo "     echo 'YOUR_ED25519_PUBKEY' >> /home/zeroscan/.ssh/authorized_keys"
echo "     chmod 600 /home/zeroscan/.ssh/authorized_keys"
echo "     chown zeroscan:zeroscan /home/zeroscan/.ssh/authorized_keys"
echo ""
echo "  2. Run on Mac: bash scripts/setup_wireguard_mac.sh $VPS_IP $VPS_PUBLIC"
echo ""
echo "  3. Add Mac WireGuard pubkey to /etc/wireguard/wg0.conf"
echo "     then: systemctl start wg-quick@wg0"
echo "═══════════════════════════════════════════════════"
