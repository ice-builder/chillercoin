"""
IIE — Signal Evaluator & TG Broadcaster

Evaluates new impulses through the AdaptivePositionManager and:
  1. Sends entry signals to Soldier (personal) + Pump Hunter group
  2. Writes pending_signals to DB for Pump Hunter to consume
  3. Sends close notifications to Pump Hunter group
"""
import time
import logging
import requests
from typing import Optional, List, Dict, Tuple

from . import config
from .impulse_db import ImpulseDB, Impulse
from .adaptive_manager import AdaptivePositionManager, TradeRecommendation

logger = logging.getLogger("iie.signals")

# Minimum OI on ANY single exchange for signal to be valid
MIN_OI_SINGLE_EXCHANGE = 1_000_000  # $1M USDT


class SignalEngine:
    """Evaluates impulses and broadcasts confirmed signals."""

    def __init__(self, db: ImpulseDB, manager: AdaptivePositionManager):
        self.db = db
        self.manager = manager
        self._last_processed_id = self._get_last_impulse_id()

    def _get_last_impulse_id(self) -> int:
        """Start from the latest impulse to avoid re-processing old ones."""
        try:
            recent = self.db.get_recent_impulses(hours=0.1, limit=1)
            return recent[0].id if recent else 0
        except Exception:
            return 0

    def evaluate_new_impulses(self) -> int:
        """
        Check for new impulses since last run, evaluate each through IIE,
        and send signals for confirmed ones.
        Returns number of signals sent.
        """
        signals_sent = 0

        # Get impulses newer than last processed
        recent = self.db.get_recent_impulses(hours=0.5, limit=50)
        if not recent:
            return 0

        for imp in reversed(recent):  # oldest first
            if imp.id <= self._last_processed_id:
                continue

            self._last_processed_id = imp.id

            # Skip if we already sent a signal for this symbol recently
            if self.db.has_recent_signal(imp.symbol, config.SIGNAL_COOLDOWN_SEC):
                continue

            # Evaluate through AdaptivePositionManager
            try:
                rec = self.manager.evaluate_signal(
                    symbol=imp.symbol,
                    direction=imp.direction,
                    vol_z=imp.vol_z,
                    ret_z=imp.ret_z,
                    rsi=imp.rsi_at_impulse,
                    combined_score=imp.combined_score,
                    ema_deviation=imp.ema_deviation_pct,
                    candle_body_pct=imp.candle_body_pct,
                    wick_top=imp.wick_ratio_top,
                    wick_bottom=imp.wick_ratio_bottom,
                    impulse_location=imp.impulse_location,
                    atr=imp.atr_at_impulse,
                    source=imp.source,
                )
            except Exception as e:
                logger.warning(f"Evaluate failed {imp.symbol}: {e}")
                continue

            if not rec.should_enter:
                continue

            if rec.score < config.SIGNAL_MIN_SCORE:
                continue

            if rec.stop_hunt_prob * 100 > config.SIGNAL_MAX_STOP_HUNT_PROB:
                logger.info(f"⚠️ {imp.symbol} blocked: stop_hunt_prob={rec.stop_hunt_prob:.0%}")
                continue

            # OI filter: fetch from all exchanges, require $1M+ on at least one
            oi_data = _fetch_oi_all_exchanges(imp.symbol)
            max_oi = max(oi_data.values()) if oi_data else 0
            total_oi = sum(oi_data.values())
            if max_oi < MIN_OI_SINGLE_EXCHANGE:
                logger.info(
                    f"⚠️ {imp.symbol} blocked: max OI=${max_oi/1e6:.2f}M "
                    f"< ${MIN_OI_SINGLE_EXCHANGE/1e6:.0f}M"
                )
                continue

            # Write signal to DB for Pump Hunter to pick up
            try:
                sig_id = self.db.insert_signal(
                    impulse_id=imp.id,
                    symbol=imp.symbol,
                    exchange=imp.exchange,
                    direction=imp.direction,
                    price=imp.price_at_impulse,
                    score=rec.score,
                    confidence=rec.confidence,
                    sl_pct=rec.recommended_sl_pct,
                    tp_pct=rec.recommended_tp_pct,
                    trail_pct=rec.recommended_trail_pct,
                    hold_bars=rec.recommended_hold_bars,
                    size_mult=rec.position_size_mult,
                    market_phase=rec.market_phase,
                    will_continue_prob=rec.will_continue_prob,
                    stop_hunt_prob=rec.stop_hunt_prob,
                    coin_quality=rec.coin_quality,
                    reason=rec.reason,
                )
            except Exception as e:
                logger.warning(f"DB insert_signal failed: {e}")
                continue

            # Send TG to Pump Hunter group only if IIE score >= 80
            if rec.score >= 80:
                tg_text = _format_entry_signal(imp, rec, oi_data=oi_data, total_oi=total_oi)
                _send_tg(config.PH_TG_TOKEN, config.PH_TG_CHAT_ID,
                          config.PH_TG_THREAD_ID, tg_text)
            else:
                logger.info(
                    f"📝 {imp.symbol} saved to DB but not published to TG: "
                    f"score {rec.score:.0f} < 80"
                )

            signals_sent += 1
            logger.info(
                f"🧠 SIGNAL {imp.direction.upper()} {imp.symbol} "
                f"score={rec.score:.0f} conf={rec.confidence:.0f} "
                f"OI=${total_oi/1e6:.1f}M SL={rec.recommended_sl_pct:.1f}%"
            )

        return signals_sent


def send_close_notification(symbol: str, direction: str, entry_price: float,
                            exit_price: float, pnl_pct: float,
                            exit_reason: str, peak_price: float = 0,
                            held_bars: int = 0):
    """Send close notification to Pump Hunter group (Russian)."""
    emoji = "🏁" if pnl_pct >= 0 else "💀"
    pnl_cls = "+" if pnl_pct >= 0 else ""
    dir_ru = "ЛОНГ" if direction == "long" else "ШОРТ"
    peak_info = ""
    if peak_price > 0 and entry_price > 0:
        peak_pct = abs((peak_price / entry_price - 1) * 100)
        peak_info = f"\n🔝 Пик: {peak_price:.8g} (+{peak_pct:.1f}%)"

    # Exit reason translation
    exit_ru = {
        "v4_stop_loss": "Стоп-лосс",
        "v4_trailing_stop": "Трейлинг-стоп",
        "manual_close": "Ручное закрытие",
    }.get(exit_reason, exit_reason)

    text = (
        f"{emoji} <b>ЗАКРЫТИЕ — {dir_ru} {symbol}</b>\n\n"
        f"💰 Вход: <code>{entry_price:.8g}</code> → Выход: <code>{exit_price:.8g}</code>\n"
        f"📈 PnL: <b>{pnl_cls}{pnl_pct:.2f}%</b>{peak_info}\n"
        f"🛑 Причина: {exit_ru}\n"
        f"⏱ Удержание: {held_bars} баров\n\n"
        f"🧠 Результат записан в IIE для обучения"
    )

    # Pump Hunter group only
    _send_tg(config.PH_TG_TOKEN, config.PH_TG_CHAT_ID,
              config.PH_TG_THREAD_ID, text, parse_mode="HTML")


def _format_entry_signal(imp: Impulse, rec: TradeRecommendation,
                         oi_data: dict = None, total_oi: float = 0) -> str:
    """Format entry signal for Telegram (Russian)."""
    dir_emoji = "📈" if imp.direction == "long" else "📉"
    dir_icon = "🟢" if imp.direction == "long" else "🔴"
    dir_ru = "ЛОНГ" if imp.direction == "long" else "ШОРТ"

    # SL/TP prices
    if imp.price_at_impulse > 0:
        if imp.direction == "long":
            sl_price = imp.price_at_impulse * (1 - rec.recommended_sl_pct / 100)
            tp_price = imp.price_at_impulse * (1 + rec.recommended_tp_pct / 100)
        else:
            sl_price = imp.price_at_impulse * (1 + rec.recommended_sl_pct / 100)
            tp_price = imp.price_at_impulse * (1 - rec.recommended_tp_pct / 100)
    else:
        sl_price = tp_price = 0

    # Strength label (Russian)
    if rec.score >= 70:
        strength = "🔥 СИЛЬНЫЙ"
    elif rec.score >= 55:
        strength = "✅ ХОРОШИЙ"
    else:
        strength = "⚡ СРЕДНИЙ"

    text = (
        f"{dir_icon} <b>ИИ СИГНАЛ — {dir_ru} {dir_emoji}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪙 <b>{imp.symbol}</b> ({imp.exchange.upper()})\n"
        f"📊 Оценка: <b>{rec.score:.0f}/100</b> | {strength}\n"
        f"🎯 Уверенность: {rec.confidence:.0f}%\n\n"
        f"{dir_emoji} Вход: <code>{imp.price_at_impulse:.8g}</code>\n"
        f"🛑 Стоп-лосс: <code>{sl_price:.8g}</code> (-{rec.recommended_sl_pct:.1f}%)\n"
        f"🎯 Тейк-профит: <code>{tp_price:.8g}</code> (+{rec.recommended_tp_pct:.1f}%)\n"
        f"⏱ Удержание: {rec.recommended_hold_bars} баров | "
        f"Трейлинг: {rec.recommended_trail_pct:.2f}%\n\n"
        f"💰 Размер позиции: <b>{rec.position_size_mult:.1f}x</b>"
    )

    if rec.position_size_reason:
        text += f" ({rec.position_size_reason})"

    text += (
        f"\n🧭 Фаза рынка: {rec.market_phase.upper()}\n\n"
        f"🤖 Продолжение: {rec.will_continue_prob:.0%} | "
        f"Стоп-хант: {rec.stop_hunt_prob:.0%}\n"
        f"🪙 Качество монеты: {rec.coin_quality:.0f}\n"
    )

    # OI breakdown by exchange
    if oi_data:
        oi_parts = []
        for exch, oi_val in sorted(oi_data.items(), key=lambda x: -x[1]):
            if oi_val > 0:
                oi_parts.append(f"{exch}: ${oi_val/1e6:.1f}M")
        if oi_parts:
            text += f"📊 OI: {' | '.join(oi_parts)}\n"
            text += f"📊 OI итого: ${total_oi/1e6:.1f}M\n"

    text += (
        f"\n⚡ vol_z={imp.vol_z:.1f} ret_z={imp.ret_z:.1f} "
        f"score={imp.combined_score:.1f} [{imp.timeframe}m]"
    )

    return text


def _send_tg(token: str, chat_id: str, thread_id: str,
             text: str, parse_mode: str = "HTML") -> bool:
    """Send message to Telegram. Returns True on success."""
    if not token or not chat_id:
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if thread_id:
        try:
            payload["message_thread_id"] = int(thread_id)
        except (ValueError, TypeError):
            pass

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=15)
        if resp.status_code == 200:
            return True
        else:
            logger.warning(f"TG send failed ({resp.status_code}): {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"TG send error: {e}")
    return False


# ─── Rate Limiter & Cache for Exchange API calls ───────────
_oi_cache = {}      # symbol -> (oi_data, timestamp)
_OI_CACHE_TTL = 300  # 5 minutes cache for OI data

_price_cache = {}   # (exchange, symbol) -> (price, timestamp)
_PRICE_CACHE_TTL = 30  # 30s cache for prices

_last_api_call = {}  # exchange -> last_call_timestamp
_MIN_CALL_INTERVAL = 1.0  # minimum 1s between calls to same exchange

def _rate_limit(exchange: str):
    """Simple rate limiter: sleep if calling too fast."""
    now = time.time()
    last = _last_api_call.get(exchange, 0)
    wait = _MIN_CALL_INTERVAL - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_api_call[exchange] = time.time()


def _fetch_oi_all_exchanges(symbol: str) -> Dict[str, float]:
    """
    Fetch open interest from multiple exchanges.
    Returns dict: {exchange_name: oi_in_usdt}.
    Cached for 5 minutes per symbol.
    """
    # Check cache
    cached = _oi_cache.get(symbol)
    if cached and time.time() - cached[1] < _OI_CACHE_TTL:
        return cached[0]

    oi = {}

    # Bybit
    try:
        _rate_limit("bybit")
        resp = requests.get("https://api.bybit.com/v5/market/open-interest",
                            params={"category": "linear", "symbol": symbol,
                                    "intervalTime": "5min", "limit": 1}, timeout=5)
        data = resp.json()
        if data.get("retCode") == 0:
            rows = data.get("result", {}).get("list", [])
            if rows:
                oi_qty = float(rows[0].get("openInterest", 0))
                # OI is in contracts, need price to get USD value
                price = _get_price_bybit(symbol)
                oi["Bybit"] = oi_qty * price if price else oi_qty
    except Exception:
        pass

    # Binance
    try:
        _rate_limit("binance")
        resp = requests.get("https://fapi.binance.com/fapi/v1/openInterest",
                            params={"symbol": symbol}, timeout=5)
        data = resp.json()
        if "openInterest" in data:
            oi_qty = float(data["openInterest"])
            price = float(data.get("price", 0))
            if price == 0:
                price = _get_price_binance(symbol)
            oi["Binance"] = oi_qty * price if price else oi_qty
    except Exception:
        pass

    # OKX
    try:
        # OKX uses different symbol format: e.g. BTC-USDT-SWAP
        base = symbol.replace("USDT", "")
        okx_sym = f"{base}-USDT-SWAP"
        _rate_limit("okx")
        resp = requests.get("https://www.okx.com/api/v5/public/open-interest",
                            params={"instType": "SWAP", "instId": okx_sym}, timeout=5)
        data = resp.json()
        if data.get("code") == "0" and data.get("data"):
            oi_qty = float(data["data"][0].get("oi", 0))
            price = _get_price_bybit(symbol)  # fallback to bybit price
            oi["OKX"] = oi_qty * price if price else oi_qty
    except Exception:
        pass

    # Bitget
    try:
        _rate_limit("bitget")
        resp = requests.get("https://api.bitget.com/api/v2/mix/market/open-interest",
                            params={"symbol": symbol, "productType": "USDT-FUTURES"},
                            timeout=5)
        data = resp.json()
        if data.get("code") == "00000" and data.get("data"):
            oi_usd = float(data["data"].get("openInterestUsd", 0))
            if oi_usd > 0:
                oi["Bitget"] = oi_usd
    except Exception:
        pass

    # Cache result
    _oi_cache[symbol] = (oi, time.time())
    return oi


def _get_price_bybit(symbol: str) -> float:
    """Get last price from Bybit (cached 30s)."""
    cache_key = ("bybit", symbol)
    cached = _price_cache.get(cache_key)
    if cached and time.time() - cached[1] < _PRICE_CACHE_TTL:
        return cached[0]

    try:
        _rate_limit("bybit_price")
        resp = requests.get("https://api.bybit.com/v5/market/tickers",
                            params={"category": "linear", "symbol": symbol},
                            timeout=3)
        data = resp.json()
        if data.get("retCode") == 0:
            tickers = data.get("result", {}).get("list", [])
            if tickers:
                price = float(tickers[0].get("lastPrice", 0))
                _price_cache[cache_key] = (price, time.time())
                return price
    except Exception:
        pass
    return _price_cache.get(cache_key, (0, 0))[0]


def _get_price_binance(symbol: str) -> float:
    """Get last price from Binance Futures (cached 30s)."""
    cache_key = ("binance", symbol)
    cached = _price_cache.get(cache_key)
    if cached and time.time() - cached[1] < _PRICE_CACHE_TTL:
        return cached[0]

    try:
        _rate_limit("binance_price")
        resp = requests.get("https://fapi.binance.com/fapi/v1/ticker/price",
                            params={"symbol": symbol}, timeout=3)
        data = resp.json()
        price = float(data.get("price", 0))
        if price > 0:
            _price_cache[cache_key] = (price, time.time())
        return price
    except Exception:
        return _price_cache.get(cache_key, (0, 0))[0]

