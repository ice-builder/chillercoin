"""
Scalper Pro — Telegram Bot (command handler)

Separate TG bot for Scalper Pro with commands:
  /status  — current state (balance, PnL, positions)
  /today   — today's trades summary
  /hyp     — hypothesis stats
  /compare — Scalper Pro vs Soldier comparison
  /last    — last N trades with checkpoints
  /kill    — emergency stop (confirmation required)
  /resume  — resume trading after kill
"""
import json
import time
import logging
import threading
from datetime import datetime, timezone, timedelta

import requests

import config
from iie_v2.database import ScalperProDB

# Kill switch file path — checked by main loop every tick
KILL_SWITCH_FILE = config.DATA_DIR / "kill_switch.flag"

logger = logging.getLogger("scalper.tgbot")


class ScalperProTGBot:
    """Telegram bot for Scalper Pro commands (polling mode)."""

    def __init__(self, db: ScalperProDB):
        self.token = config.TG_BOT_TOKEN
        self.db = db
        self.offset = 0
        self._running = False

    def start(self):
        """Start polling in background thread."""
        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()
        logger.info("🤖 TG command bot started")

    def stop(self):
        self._running = False

    @staticmethod
    def is_killed() -> bool:
        """Check if kill switch is active (used by main loop)."""
        return KILL_SWITCH_FILE.exists()

    def _poll_loop(self):
        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    self._handle_update(update)
            except Exception as e:
                logger.error(f"TG poll error: {e}")
            time.sleep(2)

    def _get_updates(self) -> list:
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{self.token}/getUpdates",
                params={"offset": self.offset, "timeout": 10},
                timeout=15,
            )
            data = resp.json()
            updates = data.get("result", [])
            if updates:
                self.offset = updates[-1]["update_id"] + 1
            return updates
        except Exception:
            return []

    def _handle_update(self, update: dict):
        # Handle callback queries (inline keyboard button presses)
        callback = update.get("callback_query")
        if callback:
            self._handle_callback(callback)
            return

        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = msg.get("chat", {}).get("id")
        if not chat_id or not text:
            return

        if text.startswith("/status"):
            self._cmd_status(chat_id)
        elif text.startswith("/today"):
            self._cmd_today(chat_id)
        elif text.startswith("/hyp"):
            self._cmd_hypotheses(chat_id)
        elif text.startswith("/last"):
            self._cmd_last(chat_id)
        elif text.startswith("/compare"):
            self._cmd_compare(chat_id)
        elif text.startswith("/kill"):
            self._cmd_kill(chat_id)
        elif text.startswith("/resume"):
            self._cmd_resume(chat_id)
        elif text.startswith("/start") or text.startswith("/help"):
            killed = "🔴 ОСТАНОВЛЕН" if self.is_killed() else "🟢 Торгует"
            self._send(chat_id,
                f"🧪 *Scalper Pro Bot v2.0*\n"
                f"Статус: {killed}\n\n"
                "Команды:\n"
                "/status — состояние\n"
                "/today — сделки за сегодня\n"
                "/hyp — гипотезы IIE v2\n"
                "/last — последние сделки\n"
                "/compare — сравнение с Soldier\n"
                "\n⚠️ Управление:\n"
                "/kill — аварийная остановка\n"
                "/resume — возобновить торговлю"
            )

    def _cmd_status(self, chat_id: int):
        stats = self.db.get_stats()
        try:
            state_data = json.loads(config.STATE_FILE.read_text())
        except Exception:
            state_data = {}

        balance = state_data.get("balance", config.VIRTUAL_BALANCE)
        pnl = state_data.get("total_pnl_pct", 0)
        wins = state_data.get("wins", 0)
        losses = state_data.get("losses", 0)
        active = len(state_data.get("active_positions", {}))
        wr = wins / max(1, wins + losses) * 100

        # Active positions
        pos_lines = ""
        for sym, pos in state_data.get("active_positions", {}).items():
            entry = pos.get("entry_price", 0)
            direction = pos.get("direction", "?")
            pos_lines += f"  {sym} {direction.upper()} @ {entry:.6g}\n"

        self._send(chat_id,
            f"🧪 *SCALPER PRO — СТАТУС*\n\n"
            f"💰 Баланс: ${balance:,.2f}\n"
            f"📈 PnL: {pnl:+.3f}%\n"
            f"📊 W{wins}/L{losses} | WR: {wr:.0f}%\n"
            f"🎯 Активных: {active}/{config.MAX_POSITIONS}\n"
            f"{pos_lines}\n"
            f"🧠 Гипотез: {stats['hypotheses_total']} "
            f"(зрелых: {stats['hypotheses_mature']})\n"
            f"📋 Ожидают чекпоинтов: {stats['pending_checkpoints']}"
        )

    def _cmd_today(self, chat_id: int):
        trades = self.db.get_closed_trades(limit=50)
        now_msk = datetime.now(timezone.utc) + timedelta(hours=config.MSK_UTC_OFFSET)
        today_str = now_msk.strftime("%Y-%m-%d")

        today_trades = [
            t for t in trades
            if datetime.fromtimestamp(t["exit_time"], tz=timezone.utc)
                .strftime("%Y-%m-%d") == today_str
        ] if trades else []

        if not today_trades:
            self._send(chat_id, "📊 Сегодня сделок нет")
            return

        wins = sum(1 for t in today_trades if t["pnl_pct_after_commission"] > 0)
        total_pnl = sum(t["pnl_pct_after_commission"] for t in today_trades)
        wr = wins / len(today_trades) * 100

        lines = f"📊 *СЕГОДНЯ: {len(today_trades)} сделок*\n"
        lines += f"WR: {wr:.0f}% | PnL: {total_pnl:+.3f}%\n\n"

        for t in today_trades[:10]:
            icon = "✅" if t["pnl_pct_after_commission"] > 0 else "❌"
            lines += (
                f"{icon} {t['symbol']} {t['direction'].upper()} "
                f"{t['pnl_pct_after_commission']:+.2f}% "
                f"({t['exit_reason']})\n"
            )

        self._send(chat_id, lines)

    def _cmd_hypotheses(self, chat_id: int):
        hyps = self.db.get_all_hypotheses(mature_only=True)
        if not hyps:
            self._send(chat_id, "🧠 Зрелых гипотез пока нет (нужно >= 10 сделок)")
            return

        lines = f"🧠 *ГИПОТЕЗЫ IIE v2* ({len(hyps)} зрелых)\n\n"
        for h in hyps[:15]:
            icon = "🟢" if h["win_rate"] >= 55 else "🟡" if h["win_rate"] >= 45 else "🔴"
            lines += (
                f"{icon} {h['symbol']} {h['direction']} {h['score_bin']}\n"
                f"   WR: {h['win_rate']:.0f}% | PnL: {h['avg_pnl']:+.2f}% | "
                f"N={h['sample_count']}\n"
                f"   SL={h['optimal_sl_pct']:.2f}% TP={h['optimal_tp_pct']:.2f}%\n"
            )

        self._send(chat_id, lines)

    def _cmd_last(self, chat_id: int):
        trades = self.db.get_closed_trades(limit=5)
        if not trades:
            self._send(chat_id, "📋 Сделок пока нет")
            return

        lines = f"📋 *ПОСЛЕДНИЕ {len(trades)} СДЕЛОК*\n\n"
        for t in trades:
            icon = "✅" if t["pnl_pct_after_commission"] > 0 else "❌"
            verified = "✅" if t.get("entry_verified") else "⚠️"

            # Get checkpoints
            cps = self.db.get_trade_checkpoints(t["id"])
            cp_line = ""
            for cp in cps:
                if cp["completed"] and cp["phase"] == "after_open":
                    cp_icon = "📈" if cp["pnl_vs_entry"] > 0 else "📉"
                    cp_line += f"  {cp_icon} +{cp['label']}: {cp['pnl_vs_entry']:+.2f}%\n"

            lines += (
                f"{icon} {t['symbol']} {t['direction'].upper()}\n"
                f"   PnL: {t['pnl_pct_after_commission']:+.3f}% "
                f"| {t['exit_reason']}\n"
                f"   Верификация: {verified}\n"
                f"{cp_line}\n"
            )

        self._send(chat_id, lines)

    def _cmd_compare(self, chat_id: int):
        # Read Soldier state
        soldier_state_path = "/home/trader/soldier/paper_state_multi.json"
        try:
            with open(soldier_state_path) as f:
                soldier = json.load(f)
        except Exception:
            soldier = {}

        # Scalper Pro state
        try:
            sp = json.loads(config.STATE_FILE.read_text())
        except Exception:
            sp = {}

        s_wins = soldier.get("wins", 0)
        s_losses = soldier.get("losses", 0)
        s_pnl = soldier.get("total_pnl_pct", 0)
        s_wr = s_wins / max(1, s_wins + s_losses) * 100

        p_wins = sp.get("wins", 0)
        p_losses = sp.get("losses", 0)
        p_pnl = sp.get("total_pnl_pct", 0)
        p_wr = p_wins / max(1, p_wins + p_losses) * 100

        self._send(chat_id,
            f"⚔️ *СРАВНЕНИЕ СТРАТЕГИЙ*\n\n"
            f"*Soldier:*\n"
            f"  PnL: {s_pnl:+.3f}% | WR: {s_wr:.0f}%\n"
            f"  W{s_wins}/L{s_losses}\n\n"
            f"*Scalper Pro:*\n"
            f"  PnL: {p_pnl:+.3f}% | WR: {p_wr:.0f}%\n"
            f"  W{p_wins}/L{p_losses}\n\n"
            f"{'🏆 Scalper Pro лучше!' if p_pnl > s_pnl else '🏆 Soldier лучше!'}"
        )

    # ── Kill Switch Commands ──────────────────────────────────────────────

    def _cmd_kill(self, chat_id: int):
        """Send emergency stop confirmation with inline button."""
        if self.is_killed():
            self._send(chat_id, "🔴 Бот уже остановлен. /resume для возобновления.")
            return

        self._send_with_keyboard(
            chat_id,
            "🚨 *АВАРИЙНАЯ ОСТАНОВКА*\n\n"
            "Бот прекратит открывать новые позиции.\n"
            "Активные позиции будут закрыты при следующем тике.\n\n"
            "⚠️ Нажмите кнопку для подтверждения:",
            [[{"text": "🔴 ПОДТВЕРДИТЬ ОСТАНОВКУ", "callback_data": "kill_confirm"}]],
        )

    def _cmd_resume(self, chat_id: int):
        """Resume trading by removing kill switch."""
        if not self.is_killed():
            self._send(chat_id, "🟢 Бот уже торгует, остановка не активна.")
            return

        try:
            KILL_SWITCH_FILE.unlink()
            logger.warning("🟢 Kill switch REMOVED via /resume command")
            self._send(chat_id,
                "🟢 *ТОРГОВЛЯ ВОЗОБНОВЛЕНА*\n\n"
                "Kill switch снят. Бот продолжит торговлю\n"
                "на следующем тике (до 15 сек)."
            )
        except Exception as e:
            self._send(chat_id, f"❌ Ошибка: {e}")

    def _handle_callback(self, callback: dict):
        """Handle inline keyboard button presses."""
        cb_id = callback.get("id")
        data = callback.get("data", "")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")

        # Answer callback to remove loading state
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/answerCallbackQuery",
                json={"callback_query_id": cb_id},
                timeout=10,
            )
        except Exception:
            pass

        if not chat_id:
            return

        if data == "kill_confirm":
            self._execute_kill(chat_id)

    def _execute_kill(self, chat_id: int):
        """Activate kill switch — stops all trading."""
        try:
            # Write kill switch file with timestamp and reason
            kill_data = {
                "killed_at": datetime.now(timezone.utc).isoformat(),
                "reason": "manual_telegram_kill",
                "chat_id": chat_id,
            }
            KILL_SWITCH_FILE.write_text(json.dumps(kill_data, indent=2))

            logger.warning("🔴 KILL SWITCH ACTIVATED via Telegram!")

            # Read current state for report
            try:
                state_data = json.loads(config.STATE_FILE.read_text())
            except Exception:
                state_data = {}

            active = len(state_data.get("active_positions", {}))
            balance = state_data.get("balance", 0)
            pnl = state_data.get("total_pnl_pct", 0)

            self._send(chat_id,
                f"🔴 *БОТА ОСТАНОВЛЕН*\n\n"
                f"⏱ Время: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n"
                f"💰 Баланс: ${balance:,.2f}\n"
                f"📈 PnL: {pnl:+.3f}%\n"
                f"📋 Активных позиций: {active}\n\n"
                f"Новые позиции не будут открываться.\n"
                f"Активные позиции будут закрыты по рынку.\n\n"
                f"Для возобновления: /resume"
            )
        except Exception as e:
            logger.error(f"Kill switch activation failed: {e}")
            self._send(chat_id, f"❌ Ошибка активации: {e}")

    def _send(self, chat_id: int, text: str):
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=15,
            )
        except Exception as e:
            logger.error(f"TG send failed: {e}")

    def _send_with_keyboard(self, chat_id: int, text: str, buttons: list):
        """Send message with inline keyboard."""
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "reply_markup": {
                        "inline_keyboard": buttons,
                    },
                },
                timeout=15,
            )
        except Exception as e:
            logger.error(f"TG send failed: {e}")
