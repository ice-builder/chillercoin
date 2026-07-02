#!/bin/bash
# ═══════════════════════════════════════════════
# $CHILLER — One-click deploy to VPS
# Usage: ./deploy.sh [landing|dashboard|bot|all]
# ═══════════════════════════════════════════════

set -e

# Config
VPS_USER="chiller"
VPS_HOST="89.23.107.214"
VPS_PORT="2847"
SSH_KEY="$HOME/.ssh/chiller_vps"
REMOTE_BASE="/opt/chiller"

# Colors
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

SSH_CMD="ssh -i $SSH_KEY -p $VPS_PORT $VPS_USER@$VPS_HOST"
SCP_CMD="scp -i $SSH_KEY -P $VPS_PORT"

log() { echo -e "${CYAN}[DEPLOY]${NC} $1"; }
ok()  { echo -e "${GREEN}  ✅ $1${NC}"; }
err() { echo -e "${RED}  ❌ $1${NC}"; exit 1; }

# Check SSH
check_connection() {
  log "Testing SSH connection..."
  $SSH_CMD 'echo connected' > /dev/null 2>&1 || err "Cannot connect to VPS"
  ok "VPS reachable"
}

# Deploy Landing
deploy_landing() {
  log "Deploying landing page..."
  $SCP_CMD -r ../chiller-landing/* $VPS_USER@$VPS_HOST:$REMOTE_BASE/landing/ 2>&1
  ok "Landing deployed → http://$VPS_HOST"
}

# Deploy Dashboard
deploy_dashboard() {
  log "Deploying dashboard..."
  $SCP_CMD -r ./index.html ./style.css ./app.js ./logo.png $VPS_USER@$VPS_HOST:$REMOTE_BASE/dashboard/ 2>&1
  ok "Dashboard deployed → http://$VPS_HOST:8080"
}

# Deploy TG Bot
deploy_bot() {
  log "Deploying TG Bot..."
  $SCP_CMD -r ../chiller-tg-bot/* $VPS_USER@$VPS_HOST:$REMOTE_BASE/bots/ 2>&1
  log "Installing dependencies on VPS..."
  $SSH_CMD "cd $REMOTE_BASE/bots && python3 -m venv venv 2>/dev/null; source venv/bin/activate && pip install -r requirements.txt -q" 2>&1
  ok "TG Bot deployed"
}

# Reload Nginx
reload_nginx() {
  log "Reloading Nginx..."
  $SSH_CMD 'sudo nginx -t && sudo systemctl reload nginx' 2>&1
  ok "Nginx reloaded"
}

# Main
TARGET="${1:-all}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo -e "${CYAN}🧊 \$CHILLER DEPLOY${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

check_connection

case "$TARGET" in
  landing)
    deploy_landing
    reload_nginx
    ;;
  dashboard)
    deploy_dashboard
    reload_nginx
    ;;
  bot)
    deploy_bot
    ;;
  all)
    deploy_landing
    deploy_dashboard
    deploy_bot 2>/dev/null || log "Bot not found, skipping"
    reload_nginx
    ;;
  *)
    echo "Usage: ./deploy.sh [landing|dashboard|bot|all]"
    exit 1
    ;;
esac

echo ""
echo -e "${GREEN}🚀 Deploy complete!${NC}"
echo ""
