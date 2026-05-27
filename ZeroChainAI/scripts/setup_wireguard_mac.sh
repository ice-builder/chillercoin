#!/bin/bash
# ============================================================
# ZeroChainAI — WireGuard Mac Client Setup
# Run on your Mac after running setup_vps.sh on the VPS
# Usage: bash setup_wireguard_mac.sh <VPS_IP> <VPS_PUBLIC_KEY>
# ============================================================
set -euo pipefail

VPS_IP="${1:?Usage: $0 <VPS_IP> <VPS_PUBLIC_KEY>}"
VPS_PUBKEY="${2:?Usage: $0 <VPS_IP> <VPS_PUBLIC_KEY>}"
WG_DIR="$HOME/.config/wireguard"
CONFIG_NAME="zeroscan"

echo "═══════════════════════════════════════════════════"
echo "  ZeroChainAI — WireGuard Mac Setup"
echo "  VPS: $VPS_IP"
echo "═══════════════════════════════════════════════════"

# Check WireGuard installed
if ! command -v wg &>/dev/null; then
    echo "Installing WireGuard..."
    brew install wireguard-tools
fi

# Create config directory
mkdir -p "$WG_DIR"
chmod 700 "$WG_DIR"

# Generate Mac keypair
MAC_PRIVATE=$(wg genkey)
MAC_PUBLIC=$(echo "$MAC_PRIVATE" | wg pubkey)

echo "📱 Mac Public Key: $MAC_PUBLIC"
echo ""

# Create Mac WireGuard config
cat > "$WG_DIR/${CONFIG_NAME}.conf" <<CONF
[Interface]
PrivateKey = ${MAC_PRIVATE}
Address = 10.99.0.2/24
DNS = 1.1.1.1

[Peer]
PublicKey = ${VPS_PUBKEY}
Endpoint = ${VPS_IP}:51820
AllowedIPs = 10.99.0.0/24
PersistentKeepalive = 25
CONF
chmod 600 "$WG_DIR/${CONFIG_NAME}.conf"

echo "✅ Mac WireGuard config created: $WG_DIR/${CONFIG_NAME}.conf"
echo ""
echo "═══════════════════════════════════════════════════"
echo "  NEXT: Add Mac pubkey to VPS"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Run on VPS (as root or via sudo):"
echo ""
echo "  wg set wg0 peer ${MAC_PUBLIC} allowed-ips 10.99.0.2/32"
echo "  wg-quick save wg0"
echo ""
echo "  OR add to /etc/wireguard/wg0.conf:"
echo ""
echo "  [Peer]"
echo "  PublicKey = ${MAC_PUBLIC}"
echo "  AllowedIPs = 10.99.0.2/32"
echo ""
echo "  Then on VPS: systemctl start wg-quick@wg0"
echo ""
echo "═══════════════════════════════════════════════════"
echo "  AFTER VPS IS CONFIGURED, test connection:"
echo ""
echo "  # Start VPN:"
echo "  sudo wg-quick up $WG_DIR/${CONFIG_NAME}.conf"
echo ""
echo "  # Test ping to VPS via VPN:"
echo "  ping 10.99.0.1"
echo ""
echo "  # Test API access:"
echo "  curl http://10.99.0.1:8443/health"
echo ""
echo "  # Stop VPN:"
echo "  sudo wg-quick down $WG_DIR/${CONFIG_NAME}.conf"
echo "═══════════════════════════════════════════════════"
echo ""
echo "  Also add to ~/.zprofile:"
echo "  export ZEROSCAN_API_URL=http://10.99.0.1:8443"
echo "  export ZEROSCAN_API_KEY=<your-key-from-vault>"
