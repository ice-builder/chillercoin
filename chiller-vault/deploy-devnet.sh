#!/usr/bin/env bash
# Devnet deploy helper — does NOT rewrite program IDs in source.
# Usage:
#   CLUSTER=devnet ./deploy-devnet.sh          # build + show plan
#   CLUSTER=devnet DEPLOY=1 ./deploy-devnet.sh # deploy existing program id
#
# Required: Solana CLI, funded deployer (faucet or own SOL).
# Optional: DEPLOYER_KEYPAIR (default ~/.config/solana/id.json)
set -euo pipefail

export PATH="$HOME/.local/share/solana/install/active_release/bin:$PATH"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
log() { echo -e "${CYAN}[DEPLOY]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✅ $1${NC}"; }
err() { echo -e "${RED}  ❌ $1${NC}"; exit 1; }
warn(){ echo -e "${YELLOW}  ⚠️  $1${NC}"; }

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

CLUSTER="${CLUSTER:-devnet}"
RPC="${RPC_URL:-https://api.devnet.solana.com}"
DEPLOYER_KEYPAIR="${DEPLOYER_KEYPAIR:-$HOME/.config/solana/id.json}"
PROGRAM_ID="${PROGRAM_ID:-7ayYqgiiBtXdk13f9DBFTxJoYKkZyr3AaaLt2f2TPDoH}"
SO="${SO:-$ROOT/target/deploy/chiller_vault.so}"
DO_DEPLOY="${DEPLOY:-0}"

command -v solana >/dev/null 2>&1 || err "Solana CLI not found"
[[ -f "$DEPLOYER_KEYPAIR" ]] || err "Missing deployer keypair: $DEPLOYER_KEYPAIR"

solana config set --url "$RPC" >/dev/null
WALLET=$(solana-keygen pubkey "$DEPLOYER_KEYPAIR")
ok "cluster=$CLUSTER wallet=$WALLET program=$PROGRAM_ID"

# Faucet (best-effort) then require a small balance for deploy
solana airdrop 2 "$WALLET" --url "$RPC" >/dev/null 2>&1 || warn "airdrop skipped/failed (rate limit is normal)"
BALANCE=$(solana balance "$WALLET" --url "$RPC" 2>/dev/null | awk '{print $1}')
ok "balance=${BALANCE:-unknown} SOL"

if [[ ! -f "$SO" ]]; then
  command -v cargo-build-sbf >/dev/null 2>&1 || err "cargo-build-sbf not found and $SO missing"
  log "Building BPF (program id in source is not rewritten)..."
  cargo-build-sbf --manifest-path programs/chiller-vault/Cargo.toml
  [[ -f "$SO" ]] || err "build did not produce $SO"
fi
ok "BPF $(wc -c < "$SO") bytes"

if [[ "$DO_DEPLOY" != "1" ]]; then
  warn "Dry run. To deploy: DEPLOY=1 $0"
  warn "This script never sed-rewrites declare_id / Anchor.toml."
  warn "After deploy: transfer upgrade authority to a multisig before accepting value."
  exit 0
fi

NEED=2
if awk "BEGIN {exit !($BALANCE < $NEED)}"; then
  err "Need ~${NEED} SOL to deploy. Faucet: https://faucet.solana.com  address $WALLET"
fi

log "Deploying $SO as $PROGRAM_ID (upgrade authority = deployer)..."
solana program deploy "$SO" \
  --program-id "$PROGRAM_ID" \
  --url "$RPC" \
  --keypair "$DEPLOYER_KEYPAIR"

ok "Deployed. Explorer: https://explorer.solana.com/address/$PROGRAM_ID?cluster=devnet"
echo "Next: initialize vault, then transfer upgrade authority to multisig."
echo "Do not run the full test glob against shared devnet."
