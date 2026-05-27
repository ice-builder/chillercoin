"""
🛑 Telegram Command Bot — HQ Control Panel for Paper Trader (Soldier).

Uses ReplyKeyboard (text buttons) instead of InlineKeyboard to avoid
conflicts with profitrade-trader which captures all callback_query events.

Usage:
    python telegram_command_bot.py

Env vars (from .env):
    TELEGRAM_SCALPER_BOT_TOKEN
    TELEGRAM_CHAT_ID
"""
import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

import requests
from dotenv import load_dotenv

load_dotenv()


def _esc(text: str) -> str:
    """Escape Markdown v1 special characters in dynamic strings."""
    return (str(text)
            .replace('_', r'\_')
            .replace('*', r'\*')
            .replace('`', r'\`')
            .replace('[', r'\['))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("TelegramHQ")

# ─── Config ──────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_SCALPER_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_DIR = Path(__file__).parent / ".local_ai" / "paper_trading"
STATE_FILE = STATE_DIR / "paper_state_multi.json"
KILL_FILE = STATE_DIR / ".kill_switch"
HISTORY_FILE = STATE_DIR / "strategy_history.json"
API = f"https://api.telegram.org/bot{TOKEN}"


# ─── Telegram Helpers ────────────────────────────────────────

def send_message(text: str, chat_id: str = CHAT_ID, keyboard: bool = True):
    payload: Dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        is_stopped = KILL_FILE.exists()
        payload["reply_markup"] = json.dumps({
            "keyboard": [
                ["🛑 STOP" if not is_stopped else "▶️ RESUME", "📊 Status"],
                ["📋 Trades", "🔬 Analyze"],
                ["📜 History", "💀 Close All"],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        })
    try:
        resp = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
        if resp.status_code != 200:
            logger.warning(f"TG send error: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"TG send failed: {e}")


def send_document(file_path: Path, caption: str = "", chat_id: str = CHAT_ID):
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"{API}/sendDocument",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
                files={"document": (file_path.name, f)},
                timeout=30,
            )
        if resp.status_code != 200:
            logger.warning(f"TG document error: {resp.status_code}")
    except Exception as e:
        logger.warning(f"TG document failed: {e}")


def register_commands():
    """Register bot commands in Telegram menu."""
    commands = [
        {"command": "stop", "description": "🛑 Emergency stop trading"},
        {"command": "resume", "description": "▶️ Resume trading"},
        {"command": "status", "description": "📊 Current status & PnL"},
        {"command": "trades", "description": "📋 All completed trades"},
        {"command": "analyze", "description": "🔬 Trade analysis"},
        {"command": "history", "description": "📜 Strategy version history"},
        {"command": "closeall", "description": "💀 Force close all positions"},
        {"command": "rollback", "description": "🔄 Rollback to version: /rollback v1"},
    ]
    try:
        resp = requests.post(f"{API}/setMyCommands", json={"commands": commands}, timeout=10)
        if resp.status_code == 200:
            logger.info("✅ Bot commands registered")
        else:
            logger.warning(f"setMyCommands failed: {resp.status_code}")
    except Exception as e:
        logger.warning(f"setMyCommands error: {e}")


# ─── State Helpers ───────────────────────────────────────────

def read_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def write_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

def read_history() -> dict:
    if not HISTORY_FILE.exists():
        return {"current_version": "v1", "versions": []}
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"current_version": "v1", "versions": []}


# ─── Command Handlers ────────────────────────────────────────

def handle_stop(chat_id: str):
    KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILL_FILE.write_text(json.dumps({
        "stopped_at": datetime.now(timezone.utc).isoformat(),
        "reason": "Manual emergency stop via Telegram HQ",
    }), encoding="utf-8")
    logger.info("🛑 KILL SWITCH ACTIVATED")

    state = read_state()
    active = state.get("active_positions", {})
    if active:
        closed_count = len(active)
        for sym, pos in active.items():
            pos["exit_reason"] = "emergency_stop"
            pos["exit_time"] = datetime.now(timezone.utc).isoformat()
            trades = state.get("completed_trades", [])
            trades.append(pos)
            state["completed_trades"] = trades
        state["active_positions"] = {}
        write_state(state)
        msg = f"🛑 *EMERGENCY STOP ACTIVATED*\n\n⚡ Kill switch created\n💀 Force-closed {closed_count} position(s)\n🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    else:
        msg = f"🛑 *EMERGENCY STOP ACTIVATED*\n\n⚡ Kill switch created\n📭 No active positions\n🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
    send_message(msg, chat_id)


def handle_resume(chat_id: str):
    if KILL_FILE.exists():
        KILL_FILE.unlink()
    logger.info("▶️ KILL SWITCH REMOVED — Trading resumed")
    send_message(f"▶️ *TRADING RESUMED*\n\n✅ Kill switch removed\n🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}", chat_id)


def handle_status(chat_id: str):
    state = read_state()
    if not state:
        send_message("📊 *Status:* No data yet.", chat_id)
        return
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    total = wins + losses
    wr = wins / max(1, total) * 100
    pnl = state.get("total_pnl_pct", 0)
    signals = state.get("signals_seen", 0)
    updated = str(state.get("last_updated", ""))[:19]
    active = state.get("active_positions", {})
    is_stopped = KILL_FILE.exists()

    pos_lines = ""
    if active:
        for sym, pos in active.items():
            dir_icon = "🟢" if pos.get("direction") == "long" else "🔴"
            strat = _esc(pos.get("strategy_name", "?"))
            entry = pos.get('entry_price', '?')
            pos_lines += f"  {dir_icon} {_esc(sym)} @ {entry} ({strat})\n"
    else:
        pos_lines = "  — нет открытых позиций\n"

    status_icon = "🛑 ОСТАНОВЛЕН" if is_stopped else "🟢 АКТИВЕН"
    pnl_icon = "📈" if pnl >= 0 else "📉"

    # Get config version
    history = read_history()
    ver = _esc(history.get("current_version", "?"))

    msg = (
        f"📊 *SOLDIER STATUS*\n\n"
        f"Status: {status_icon}\n"
        f"📋 Config: `{ver}`\n"
        f"{pnl_icon} PnL: `{pnl:+.3f}%`\n"
        f"🎯 Trades: {total} (W{wins}/L{losses})\n"
        f"📊 Win Rate: `{wr:.1f}%`\n"
        f"🔍 Signals: {signals}\n\n"
        f"*Active ({len(active)}):*\n{pos_lines}\n"
        f"Updated: {_esc(updated)}"
    )
    send_message(msg, chat_id)


def handle_trades(chat_id: str):
    state = read_state()
    trades = state.get("completed_trades", [])
    if not trades:
        send_message("📋 No completed trades yet.", chat_id)
        return
    if len(trades) > 5:
        dump_path = STATE_DIR / "trades_dump.json"
        dump_path.write_text(json.dumps(trades, indent=2, default=str), encoding="utf-8")
        send_document(dump_path, f"📋 *All {len(trades)} trades*")
        wins = sum(1 for t in trades if t.get("realized_pnl_pct", 0) > 0)
        total_pnl = sum(t.get("realized_pnl_pct", 0) for t in trades)
        send_message(f"📋 *Summary:* {len(trades)} trades | W{wins}/L{len(trades)-wins} | PnL: `{total_pnl:+.3f}%`", chat_id)
    else:
        lines = []
        for i, t in enumerate(trades, 1):
            d = "🟢" if t.get("direction") == "long" else "🔴"
            pnl = t.get("realized_pnl_pct", 0)
            icon = "✅" if pnl > 0 else "❌"
            ver = _esc(t.get("config_version", "v?"))
            reason = _esc(t.get('exit_reason', '?'))
            sym = _esc(t.get('symbol', '?'))
            lines.append(f"{icon} #{i} {d}{sym} `{pnl:+.3f}%` ({reason}) [{ver}]")
        send_message("📋 *All Trades:*\n\n" + "\n".join(lines), chat_id)


def handle_closeall(chat_id: str):
    state = read_state()
    active = state.get("active_positions", {})
    if not active:
        send_message("💀 No active positions to close.", chat_id)
        return
    closed = []
    for sym, pos in list(active.items()):
        pos["exit_reason"] = "force_close_hq"
        pos["exit_time"] = datetime.now(timezone.utc).isoformat()
        pos["realized_pnl_pct"] = pos.get("realized_pnl_pct", 0)
        trades = state.get("completed_trades", [])
        trades.append(pos)
        state["completed_trades"] = trades
        closed.append(sym)
    state["active_positions"] = {}
    write_state(state)
    send_message(f"💀 *FORCE CLOSED {len(closed)} positions:*\n\n" + "\n".join(f"  ❌ {s}" for s in closed), chat_id)


def handle_analyze(chat_id: str):
    state = read_state()
    trades = state.get("completed_trades", [])
    if not trades:
        send_message("🔬 No trades to analyze.", chat_id)
        return
    send_message(run_trade_analysis(trades), chat_id)


def handle_history(chat_id: str):
    history = read_history()
    versions = history.get("versions", [])
    current = history.get("current_version", "?")

    if not versions:
        send_message("📜 No version history yet.", chat_id)
        return

    lines = []
    for v in versions:
        ver = _esc(v.get("version", "?"))
        verdict = _esc(v.get("verdict", ""))
        desc = _esc(v.get("description", "")[:60])
        perf = v.get("performance", {})
        trades_count = perf.get("trades", 0)
        pnl = perf.get("total_pnl_pct", 0)

        is_active = " ← *ACTIVE*" if v.get("version") == current else ""
        icon = "🟢" if v.get("version") == current else "⚪"

        if trades_count > 0:
            lines.append(f"{icon} *{ver}*{is_active}\n  {desc}\n  📊 {trades_count} trades | `{pnl:+.3f}%`\n  {verdict}")
        else:
            lines.append(f"{icon} *{ver}*{is_active}\n  {desc}\n  ⏳ Awaiting results\n  {verdict}")

        for ch in v.get("changes", []):
            lines.append(f"  🔧 `{_esc(ch['param'])}`: `{_esc(ch['old'])}` → `{_esc(ch['new'])}`")

    # Live per-version stats
    state = read_state()
    current_trades = state.get("completed_trades", [])
    if current_trades:
        by_version: Dict[str, list] = {}
        for t in current_trades:
            ver = t.get("config_version", "v1")
            by_version.setdefault(ver, []).append(t.get("realized_pnl_pct", 0))
        if by_version:
            lines.append("\n*Live Performance by Version:*")
            for ver, pnls in sorted(by_version.items()):
                total = sum(pnls)
                wins = sum(1 for p in pnls if p > 0)
                wr = wins / max(1, len(pnls)) * 100
                emoji = "📈" if total >= 0 else "📉"
                lines.append(f"  {emoji} `{_esc(ver)}`: {len(pnls)} trades | `{total:+.3f}%` | WR {wr:.0f}%")

    send_message(f"📜 *STRATEGY VERSION HISTORY*\n{'━' * 28}\n\n" + "\n\n".join(lines) + "\n\nRollback: /rollback vN", chat_id)


def handle_rollback(chat_id: str, version: str = ""):
    if not version:
        send_message("❓ Usage: `/rollback v1`", chat_id)
        return

    history = read_history()
    versions = {v["version"]: v for v in history.get("versions", [])}

    if version not in versions:
        send_message(f"❌ Version `{version}` not found. Available: {', '.join(versions.keys())}", chat_id)
        return

    target = versions[version]
    target_params = target.get("params", {})
    if not target_params:
        send_message(f"❌ Version `{version}` has no saved params.", chat_id)
        return

    old_version = history.get("current_version", "?")
    new_version = f"v{len(history.get('versions', [])) + 1}"

    history["versions"].append({
        "version": new_version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "description": f"Rollback to {version} params (from {old_version})",
        "changes": [{"param": "rollback_from", "old": old_version, "new": version, "reason": "Manual rollback via Telegram HQ"}],
        "params": target_params,
        "performance": {"trades": 0},
        "verdict": "⏳ IN PROGRESS (rollback)"
    })
    history["current_version"] = new_version
    HISTORY_FILE.write_text(json.dumps(history, indent=2, default=str), encoding="utf-8")

    opt_path = STATE_DIR / "optimized_params.json"
    opt_path.write_text(json.dumps(target_params, indent=2), encoding="utf-8")

    param_lines = "\n".join(f"  `{k}`: `{v}`" for k, v in target_params.items())
    send_message(
        f"🔄 *ROLLBACK COMPLETE*\n\n"
        f"`{old_version}` → `{new_version}` (params from `{version}`)\n\n"
        f"*Applied params:*\n{param_lines}\n\n"
        f"⚠️ _Restart trader to apply_",
        chat_id,
    )


# ─── Trade Analysis Engine ───────────────────────────────────

def run_trade_analysis(trades: List[Dict]) -> str:
    total = len(trades)
    wins = [t for t in trades if t.get("realized_pnl_pct", 0) > 0]
    losses = [t for t in trades if t.get("realized_pnl_pct", 0) <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    wr = win_count / max(1, total) * 100
    total_pnl = sum(t.get("realized_pnl_pct", 0) for t in trades)
    avg_pnl = total_pnl / max(1, total)
    avg_win = sum(t.get("realized_pnl_pct", 0) for t in wins) / max(1, win_count)
    avg_loss = sum(t.get("realized_pnl_pct", 0) for t in losses) / max(1, loss_count)
    gross_profit = sum(t.get("realized_pnl_pct", 0) for t in wins) if wins else 0
    gross_loss = abs(sum(t.get("realized_pnl_pct", 0) for t in losses)) if losses else 0
    profit_factor = gross_profit / max(0.001, gross_loss)

    # Streaks
    max_win_streak = max_loss_streak = current_streak = 0
    streak_type = None
    for t in trades:
        is_win = t.get("realized_pnl_pct", 0) > 0
        if is_win:
            current_streak = current_streak + 1 if streak_type == "win" else 1
            streak_type = "win"
            max_win_streak = max(max_win_streak, current_streak)
        else:
            current_streak = current_streak + 1 if streak_type == "loss" else 1
            streak_type = "loss"
            max_loss_streak = max(max_loss_streak, current_streak)

    # Breakdowns
    def breakdown(key):
        groups: Dict[str, list] = {}
        for t in trades:
            k = t.get(key, "unknown")
            groups.setdefault(k, []).append(t.get("realized_pnl_pct", 0))
        lines = ""
        for name, pnls in sorted(groups.items(), key=lambda x: sum(x[1])):
            s_pnl = sum(pnls)
            s_wr = sum(1 for p in pnls if p > 0) / max(1, len(pnls)) * 100
            emoji = "🟢" if s_pnl >= 0 else "🔴"
            safe_name = _esc(str(name))
            lines += f"  {emoji} {safe_name}: {len(pnls)} | `{s_pnl:+.3f}%` | WR {s_wr:.0f}%\n"
        return lines

    longs = [t for t in trades if t.get("direction") == "long"]
    shorts = [t for t in trades if t.get("direction") == "short"]
    long_pnl = sum(t.get("realized_pnl_pct", 0) for t in longs)
    short_pnl = sum(t.get("realized_pnl_pct", 0) for t in shorts)

    verdict = "✅ В плюсе" if total_pnl > 0 else "🚨 СЛИВАЕТ" if total_pnl < -1 else "⚠️ Около нуля"

    return (
        f"🔬 *TRADE ANALYSIS — HQ DEBRIEF*\n{'━' * 28}\n\n"
        f"*{verdict}* ({total_pnl:+.3f}%)\n\n"
        f"📊 WR: `{wr:.1f}%` ({win_count}W/{loss_count}L)\n"
        f"💰 Avg: Win `{avg_win:+.3f}%` / Loss `{avg_loss:+.3f}%`\n"
        f"💎 PF: `{profit_factor:.2f}` | Streaks: W{max_win_streak}/L{max_loss_streak}\n\n"
        f"*By Direction:*\n"
        f"  🟢 LONG: {len(longs)} | `{long_pnl:+.3f}%`\n"
        f"  🔴 SHORT: {len(shorts)} | `{short_pnl:+.3f}%`\n\n"
        f"*By Strategy:*\n{breakdown('strategy_name')}\n"
        f"*By Exit:*\n{breakdown('exit_reason')}\n"
        f"*By Symbol:*\n{breakdown('symbol')}"
    )


# ─── Text → Handler Mapping ─────────────────────────────────

BUTTON_MAP = {
    "🛑 stop": "stop", "🛑 стоп": "stop",
    "▶️ resume": "resume", "▶️ резюме": "resume",
    "📊 status": "status", "📊 статус": "status",
    "📋 trades": "trades", "📋 сделки": "trades",
    "🔬 analyze": "analyze", "🔬 анализ": "analyze",
    "📜 history": "history", "📜 история": "history",
    "💀 close all": "closeall", "💀 закрыть все": "closeall",
}

HANDLERS = {
    "stop": handle_stop,
    "resume": handle_resume,
    "status": handle_status,
    "trades": handle_trades,
    "closeall": handle_closeall,
    "analyze": handle_analyze,
    "history": handle_history,
}

SLASH_COMMANDS = {
    # Primary (no-prefix) commands for this bot
    "/stop": "stop", "/resume": "resume", "/status": "status",
    "/trades": "trades", "/closeall": "closeall", "/analyze": "analyze",
    "/history": "history", "/start": "status", "/help": "status",
    # Prefixed commands (safe when sharing chat with profitrade-telegram)
    "/s_stop": "stop", "/s_resume": "resume", "/s_status": "status",
    "/s_trades": "trades", "/s_closeall": "closeall", "/s_analyze": "analyze",
    "/s_history": "history",
    # /lstop is a quick alias that also works
    "/lstop": "stop",
}


# ─── Main Polling Loop ──────────────────────────────────────

def _get_initial_offset() -> int:
    try:
        resp = requests.get(f"{API}/getUpdates", params={"timeout": 0, "limit": 1, "offset": -1}, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("result", [])
            if results:
                return results[-1]["update_id"] + 1
    except Exception:
        pass
    return 0


def main():
    if not TOKEN or not CHAT_ID:
        logger.error("❌ TELEGRAM_SCALPER_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        sys.exit(1)

    logger.info(f"🤖 HQ Bot starting (short-poll)... (chat: {CHAT_ID})")

    register_commands()

    offset = _get_initial_offset()
    logger.info(f"📍 Initial offset: {offset}")

    send_message(
        "🤖 *HQ COMMAND BOT ONLINE*\n\n"
        "Используй кнопки ниже или /команды.\n"
        f"Kill switch: {'🛑 ACTIVE' if KILL_FILE.exists() else '✅ OFF'}\n\n"
        "Префиксные команды (если в чате 2 бота):\n"
        "/s\\_status | /s\\_stop | /s\\_resume | /s\\_trades",
        CHAT_ID,
    )

    poll_interval = 3

    while True:
        try:
            resp = requests.get(
                f"{API}/getUpdates",
                params={"offset": offset, "timeout": 0, "limit": 10},
                timeout=10,
            )
            if resp.status_code in (409, 429):
                time.sleep(poll_interval)
                continue
            if resp.status_code != 200:
                time.sleep(poll_interval)
                continue

            data = resp.json()
            if not data.get("ok"):
                time.sleep(poll_interval)
                continue

            for update in data.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message", {})
                text = message.get("text", "").strip()
                msg_chat = str(message.get("chat", {}).get("id", ""))

                if not text or msg_chat != CHAT_ID:
                    continue

                text_lower = text.lower()

                # /rollback with argument
                if text_lower.startswith("/rollback"):
                    parts = text.split()
                    version = parts[1] if len(parts) > 1 else ""
                    logger.info(f"📩 /rollback {version}")
                    handle_rollback(msg_chat, version)
                    continue

                # Slash commands
                cmd = text_lower.split()[0]
                action = SLASH_COMMANDS.get(cmd)
                if action:
                    handler = HANDLERS.get(action)
                    if handler:
                        logger.info(f"📩 Command: {cmd}")
                        handler(msg_chat)
                    continue

                # ReplyKeyboard button text
                action = BUTTON_MAP.get(text_lower)
                if action:
                    handler = HANDLERS.get(action)
                    if handler:
                        logger.info(f"📩 Button: {text}")
                        handler(msg_chat)

            time.sleep(poll_interval)

        except KeyboardInterrupt:
            logger.info("Bot stopped.")
            break
        except Exception as e:
            logger.error(f"Polling error: {e}", exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
