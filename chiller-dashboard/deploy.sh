#!/bin/bash
# ═══════════════════════════════════════════════
# $CHILLER — Deploy helper (ops only; no topology secrets in git)
# Usage: ./deploy.sh [landing|dashboard|bot|all]
#
# Required env (or ~/.config/chiller/deploy.env / ./.deploy.env — gitignored):
#   VPS_HOST, VPS_USER, VPS_PORT, SSH_KEY, REMOTE_BASE
# ═══════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

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
  log "Deploying landing page..."
  "${SCP_CMD[@]}" -r ../chiller-landing/* "$VPS_USER@$VPS_HOST:$REMOTE_BASE/landing/" 2>&1
  ok "Landing deployed"
}

deploy_dashboard() {
  log "Deploying dashboard..."
  "${SCP_CMD[@]}" ./index.html ./style.css ./app.js ./logo.png "$VPS_USER@$VPS_HOST:$REMOTE_BASE/dashboard/" 2>&1
  ok "Dashboard deployed"
}

deploy_bot() {
  log "Deploying bot package..."
  "${SCP_CMD[@]}" -r ../chiller-tg-bot/* "$VPS_USER@$VPS_HOST:$REMOTE_BASE/bots/" 2>&1
  log "Installing dependencies on VPS..."
  "${SSH_CMD[@]}" "cd $REMOTE_BASE/bots && python3 -m venv venv 2>/dev/null; source venv/bin/activate && pip install -r requirements.txt -q" 2>&1
  ok "Bot package deployed"
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
  bot) deploy_bot ;;
  all)
    deploy_landing
    deploy_dashboard
    deploy_bot 2>/dev/null || log "Bot package not found, skipping"
    reload_nginx
    ;;
  *)
    echo "Usage: VPS_HOST=… VPS_USER=… VPS_PORT=… SSH_KEY=… REMOTE_BASE=… ./deploy.sh [landing|dashboard|bot|all]"
    exit 1
    ;;
esac

echo ""
echo -e "${GREEN}🚀 Deploy complete!${NC}"
echo ""
