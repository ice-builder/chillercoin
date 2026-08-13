#!/bin/bash
# ═══════════════════════════════════════════════
# $CHILLER — Deploy helper (ops only; no topology secrets in git)
# Usage: ./deploy.sh [landing|dashboard|all]
#
# Required env (or ~/.config/chiller/deploy.env / ./.deploy.env — gitignored):
#   VPS_HOST, VPS_USER, VPS_PORT, SSH_KEY, REMOTE_BASE
#   LANDING_DIR — local path to private landing source (not in this repo)
# ═══════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
SITE_DIR="$(cd "$SCRIPT_DIR/../site" && pwd)"

if [[ -f "$HOME/.config/chiller/deploy.env" ]]; then
  # shellcheck disable=SC1090
  source "$HOME/.config/chiller/deploy.env"
fi
if [[ -f "$SCRIPT_DIR/.deploy.env" ]]; then
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.deploy.env"
fi

: "${VPS_HOST:?Set VPS_HOST}"
: "${VPS_USER:?Set VPS_USER}"
: "${VPS_PORT:?Set VPS_PORT}"
: "${SSH_KEY:?Set SSH_KEY path}"
: "${REMOTE_BASE:?Set REMOTE_BASE}"

GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

SSH_CMD=(ssh -i "$SSH_KEY" -p "$VPS_PORT" "$VPS_USER@$VPS_HOST")
SCP_CMD=(scp -i "$SSH_KEY" -P "$VPS_PORT")

log() { echo -e "${CYAN}[DEPLOY]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✅ $1${NC}"; }
err() { echo -e "${RED}  ❌ $1${NC}"; exit 1; }

check_connection() {
  log "Testing SSH connection..."
  "${SSH_CMD[@]}" 'echo connected' >/dev/null 2>&1 || err "Cannot connect to VPS"
  ok "VPS reachable"
}

deploy_landing() {
  : "${LANDING_DIR:?Set LANDING_DIR to the local landing source}"
  [[ -d "$LANDING_DIR" ]] || err "LANDING_DIR is not a directory"
  log "Deploying landing page..."
  "${SCP_CMD[@]}" -r "$LANDING_DIR/." "$VPS_USER@$VPS_HOST:$REMOTE_BASE/landing/"
  log "Publishing on-chain trades page onto the landing host..."
  "${SCP_CMD[@]}" "$SITE_DIR/trades.html" "$SITE_DIR/trades.css" "$SITE_DIR/trades.js" \
    "$VPS_USER@$VPS_HOST:$REMOTE_BASE/landing/"
  "${SSH_CMD[@]}" "mkdir -p '$REMOTE_BASE/landing/js'"
  "${SCP_CMD[@]}" "$SITE_DIR/js/onchain-trades.js" "$VPS_USER@$VPS_HOST:$REMOTE_BASE/landing/js/"
  ok "Landing + public tape deployed"
}

deploy_dashboard() {
  log "Deploying dashboard..."
  "${SSH_CMD[@]}" "mkdir -p '$REMOTE_BASE/dashboard/js' '$REMOTE_BASE/dashboard/data'"
  "${SCP_CMD[@]}" ./index.html ./style.css ./app.js ./manifest.json \
    "$VPS_USER@$VPS_HOST:$REMOTE_BASE/dashboard/"
  if [[ -f ./logo.png ]]; then
    "${SCP_CMD[@]}" ./logo.png "$VPS_USER@$VPS_HOST:$REMOTE_BASE/dashboard/"
  fi
  "${SCP_CMD[@]}" ./js/onchain-trades.js "$VPS_USER@$VPS_HOST:$REMOTE_BASE/dashboard/js/"
  if [[ -f ./data/onchain-trades.json ]]; then
    "${SCP_CMD[@]}" ./data/onchain-trades.json "$VPS_USER@$VPS_HOST:$REMOTE_BASE/dashboard/data/"
  fi
  ok "Dashboard deployed"
}

reload_nginx() {
  log "Reloading reverse proxy..."
  "${SSH_CMD[@]}" 'sudo nginx -t && sudo systemctl reload nginx' 2>&1
  ok "Proxy reloaded"
}

TARGET="${1:-all}"

echo ""
echo -e "${CYAN}🧊 \$CHILLER DEPLOY${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "target host configured via env"
echo ""

check_connection

case "$TARGET" in
  landing) deploy_landing; reload_nginx ;;
  dashboard) deploy_dashboard; reload_nginx ;;
  all)
    deploy_landing
    deploy_dashboard
    reload_nginx
    ;;
  *)
    echo "Usage: VPS_HOST=… VPS_USER=… VPS_PORT=… SSH_KEY=… REMOTE_BASE=… LANDING_DIR=… ./deploy.sh [landing|dashboard|all]"
    exit 1
    ;;
esac

echo ""
echo -e "${GREEN}🚀 Deploy complete!${NC}"
echo ""
