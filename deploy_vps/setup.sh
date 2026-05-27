#!/bin/bash
# ============================================================
# 🚀 Impulse Scalper — VPS Deploy Script
# ============================================================
# Run this on VPS: bash setup.sh
# ============================================================

set -e

echo "════════════════════════════════════════════════════════"
echo "  🚀 Impulse Scalper VPS Setup"
echo "════════════════════════════════════════════════════════"

# 1. Install Python & pip
echo "📦 Installing Python dependencies..."
apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv > /dev/null 2>&1
echo "✅ Python installed"

# 2. Create venv
DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DEPLOY_DIR"

if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment created"
fi

source venv/bin/activate
pip install -q pandas numpy requests python-dotenv
echo "✅ Dependencies installed"

# 3. Stop existing pm2 processes
pm2 delete scalper-trader 2>/dev/null || true
pm2 delete scalper-monitor 2>/dev/null || true

# 4. Start Trader with PM2
echo "🚀 Starting Trader with PM2..."
pm2 start venv/bin/python --name "scalper-trader" --env DYNAMIC_SYMBOLS=1 -- paper_trader.py --top 20 --max-pos 5 --interval 60

# 5. Start Monitor with PM2
echo "🖥️ Starting Monitor with PM2..."
pm2 start venv/bin/python --name "scalper-monitor" -- monitor.py --port 8585

# 6. Setup Daily Optimization Cron (using pm2 to run the script)
CRON_JOB="0 0 * * * cd $DEPLOY_DIR && DYNAMIC_SYMBOLS=1 PYTHONPATH=$DEPLOY_DIR $DEPLOY_DIR/venv/bin/python $DEPLOY_DIR/src/crypto_scalp/auto_optimizer.py >> $DEPLOY_DIR/opt.log 2>&1"
(crontab -l 2>/dev/null | grep -v "auto_optimizer.py"; echo "$CRON_JOB") | crontab -
echo "✅ Daily Auto-Optimization cron set (midnight)"

pm2 save

echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✅ DEPLOYED SUCCESSFULLY (v2: Multi-Symbol + PM2)"
echo "════════════════════════════════════════════════════════"
echo ""
echo "  📊 PM2 Status:     pm2 list"
echo "  📋 Trader Logs:    pm2 logs scalper-trader"
echo "  🖥️ Monitor Logs:   pm2 logs scalper-monitor"
echo "  📱 Telegram:      уведомления включены"
echo ""
echo "  Авто-оптимизация работает каждую полночь."
echo "════════════════════════════════════════════════════════"
