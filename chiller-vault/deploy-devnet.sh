#!/bin/bash
# ═══════════════════════════════════════════════
# Chiller Vault — Devnet Deploy & Test Script
# Usage: ./deploy-devnet.sh
# ═══════════════════════════════════════════════
set -e

export PATH="$HOME/.local/share/solana/install/active_release/bin:$PATH"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${CYAN}[DEPLOY]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✅ $1${NC}"; }
err() { echo -e "${RED}  ❌ $1${NC}"; exit 1; }
warn(){ echo -e "${YELLOW}  ⚠️  $1${NC}"; }

echo ""
echo -e "${CYAN}🧊 CHILLER VAULT — Devnet Deploy${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ─── Step 1: Check tools ───
log "Checking tools..."
command -v solana >/dev/null 2>&1 || err "Solana CLI not found"
command -v anchor >/dev/null 2>&1 || err "Anchor CLI not found"
ok "Solana $(solana --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
ok "Anchor $(anchor --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"

# ─── Step 2: Configure devnet ───
log "Configuring devnet..."
solana config set --url devnet > /dev/null 2>&1
WALLET=$(solana address 2>/dev/null)
BALANCE=$(solana balance 2>/dev/null | grep -oE '[0-9]+\.?[0-9]*')
ok "Wallet: $WALLET"
ok "Balance: $BALANCE SOL"

# Check minimum balance
if (( $(echo "$BALANCE < 2" | bc -l 2>/dev/null || echo "1") )); then
    warn "Balance too low for deploy (need ~2 SOL)"
    warn "Run: solana airdrop 2"
    warn "Or visit: https://faucet.solana.com"
    warn "Address: $WALLET"
    exit 1
fi

# ─── Step 3: Build ───
log "Building Chiller Vault..."
cd "$(dirname "$0")"
anchor build 2>&1 | tail -3
ok "Build complete"

# Get program keypair
PROGRAM_ID=$(solana-keygen pubkey target/deploy/chiller_vault-keypair.json 2>/dev/null)
ok "Program ID: $PROGRAM_ID"

# Update Anchor.toml with program ID
sed -i '' "s|chiller_vault = \".*\"|chiller_vault = \"$PROGRAM_ID\"|g" Anchor.toml
sed -i '' 's|cluster = "localnet"|cluster = "devnet"|g' Anchor.toml

# Update lib.rs declare_id
sed -i '' "s|declare_id!(\".*\")|declare_id!(\"$PROGRAM_ID\")|g" programs/chiller-vault/src/lib.rs

# Rebuild with correct ID
anchor build 2>&1 | tail -2
ok "Rebuilt with correct program ID"

# ─── Step 4: Deploy ───
log "Deploying to devnet..."
anchor deploy --provider.cluster devnet 2>&1 | tail -5

DEPLOYED_ID=$(solana program show "$PROGRAM_ID" --url devnet 2>/dev/null | grep "Program Id" | awk '{print $3}')
if [ -z "$DEPLOYED_ID" ]; then
    err "Deploy failed — program not found on devnet"
fi
ok "Deployed: $PROGRAM_ID"
ok "Explorer: https://explorer.solana.com/address/$PROGRAM_ID?cluster=devnet"

# ─── Step 5: Initialize Vault ───
log "Initializing vault..."
# Run anchor test which includes initialization
anchor test --skip-local-validator --provider.cluster devnet 2>&1 | tail -10

echo ""
echo -e "${GREEN}🚀 Chiller Vault deployed to Devnet!${NC}"
echo ""
echo "Program ID:  $PROGRAM_ID"
echo "Authority:   $WALLET"
echo "Explorer:    https://explorer.solana.com/address/$PROGRAM_ID?cluster=devnet"
echo ""
echo -e "${CYAN}Next steps:${NC}"
echo "  1. Create Squads multisig at https://app.squads.so"
echo "  2. Transfer authority to multisig"
echo "  3. Test deposit/withdraw on devnet"
echo ""
