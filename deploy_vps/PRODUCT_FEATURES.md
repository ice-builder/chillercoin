# 📋 Product Features Database — Trading Bots

> Последнее обновление: 2026-05-28

---

## 🪖 Soldier (paper_trader.py)

**Назначение**: Основной торговый бот на IIE-сигналах с мультистратегией.

### Текущая версия: v8.0

| Фича | Версия | Статус |
|-------|--------|--------|
| IIE Signal Intake (pending_signals) | v6.0 | ✅ |
| Balanced long/short signal fetch (top 10+10) | v2.0 | ✅ NEW |
| BTC macro bias filter (EMA20 vs EMA50 1h) | v4.0 | ✅ |
| Strategy Pack (15 strategies) | v7.0 | ✅ |
| Dynamic symbol discovery | v3.0 | ✅ |
| Adaptive position manager (IIE v6) | v6.0 | ✅ |
| ML predictor (trained on 5000 samples) | v5.0 | ✅ |
| Paper / Demo / Live mode switch | v1.0 | ✅ |
| Binance Testnet executor (demo mode) | v2.0 | ⚠️ Testnet мёртв → paper mode |
| Signal Hub broadcasting | v3.0 | ✅ |
| Telegram notifications | v1.0 | ✅ |
| Kill switch (.kill_switch file) | v1.0 | ✅ |
| Direction limit (MAX_SAME_DIRECTION_POS) | v2.0 | ✅ |
| IIE trail-only exit | v7.0 | ✅ |
| Optimized params hot-reload | v4.0 | ✅ |
| Strategy pack hot-reload (hourly) | v7.0 | ✅ |

### Конфигурация
- Mode: `paper` (was `demo` — Binance Testnet dead)
- Risk per trade: 0.1%
- Max positions: 5
- Max same direction: 3
- Signal source: `iie/data/impulses.db` → `pending_signals`
- Signal min score: 70
- Signal max age: 1h

---

## 🧪 Scalper Pro (scalper_pro.py)

**Назначение**: Адаптивный скальпер с IIE v2 гипотезами и self-learning.

### Текущая версия: v2.0

| Фича | Версия | Статус |
|-------|--------|--------|
| IIE Signal Intake (balanced long/short) | v2.0 | ✅ NEW |
| Signal freshness filter (max 24h) | v2.0 | ✅ NEW |
| Dynamic Stop Manager (phase-based stops) | v1.0 | ✅ |
| Stop recovery on PM2 restart | v2.0 | ✅ NEW |
| Adaptive Position Sizer | v1.0 | ✅ |
| IIE v2 Hypothesis Engine | v2.0 | ✅ |
| Partial checkpoint analysis (4/6 minimum) | v2.0 | ✅ NEW |
| Feedback Loop (auto-learning) | v2.0 | ✅ |
| Price Verifier (3-exchange median) | v1.0 | ✅ |
| Checkpoint Tracker (15m, 1h, 4h) | v1.0 | ✅ |
| Emergency stop (3% session loss) | v2.0 | ✅ |
| Max position cap (15% balance) | v2.0 | ✅ NEW |
| Portfolio exposure cap (100%) | v2.0 | ✅ NEW |
| Direction balancing (max 3 long/short) | v2.0 | ✅ NEW |
| Symbol blacklist (2 consecutive losses) | v2.0 | ✅ NEW |
| Default trail 0.8% (was 0.15%) | v2.0 | ✅ NEW |
| Risk per trade 1.5% (was 2.0%) | v2.0 | ✅ NEW |
| Telegram command bot (polling) | v1.0 | ✅ |
| /kill — emergency stop with confirmation | v2.0 | ✅ NEW |
| /resume — resume after kill | v2.0 | ✅ NEW |
| /status, /today, /hyp, /last, /compare | v1.0 | ✅ |
| TG bot command menu (setMyCommands) | v2.0 | ✅ NEW |
| Inline keyboard confirmation buttons | v2.0 | ✅ NEW |
| Hypothesis-based trail: 50% SL, min 0.5% | v2.0 | ✅ NEW |

### Конфигурация
- Virtual balance: $5,000
- Max positions: 5
- Max same direction: 3
- Emergency stop: 3%
- Max position: 15% of balance
- Trail: 0.8% default
- Signal source: shared `pending_signals` (read-only)
- Hypothesis maturity: 5 trades (was 10)

---

## 🎯 Pump Hunter (pump_scanner_v2.py)

**Назначение**: Детектор аномальных объёмных импульсов (pump/dump).

### Текущая версия: v3.0 (IIE v4 engine)

| Фича | Версия | Статус |
|-------|--------|--------|
| Volume/return z-score impulse detection | v1.0 | ✅ |
| Multi-version strategies (v1, v2, v3) | v3.0 | ✅ |
| 1156+ tickers scanning (Bybit) | v2.0 | ✅ |
| v3 impulse engine (vol_z + ret_z + score) | v3.0 | ✅ |
| Position management with TP/SL | v2.0 | ✅ |
| Demo state persistence | v1.0 | ✅ |
| 9-minute scan interval | v2.0 | ✅ |
| Max positions limit | v1.0 | ⚠️ stuck at 2/2 |

### Конфигурация
- Max positions per version: 2
- Scan interval: ~9 min
- Balance: $7,284 (+16.4% total)

---

## 🧠 IIE Engine (iie_daemon.py)

**Назначение**: Impulse Intelligence Engine — генерация торговых сигналов.

### Текущая версия: v3.0

| Фича | Версия | Статус |
|-------|--------|--------|
| Impulse Collector (multi-timeframe) | v2.0 | ✅ |
| Coin Scorer (quality profiling) | v2.0 | ✅ |
| Signal Engine (score + confidence) | v3.0 | ✅ |
| ML Impulse Predictor | v2.0 | ✅ |
| Market Phase detection | v1.0 | ✅ |
| Post-trade Outcome Tracker | v2.0 | ✅ |
| Pending Signals table (shared DB) | v1.0 | ✅ |
| Stop hunt probability filter | v2.0 | ✅ |
| OI (Open Interest) filter ($1M min) | v2.0 | ✅ |
| Adaptive Manager | v1.0 | ✅ |
| Both LONG and SHORT signal generation | v1.0 | ✅ |

### Статистика
- Unprocessed signals: ~2400
- High-score (≥70): 464 (164 long + 300 short)
- Timeframes: 5m, 15m, 30m, 1h

---

## 📡 Signal Hub

**Назначение**: Централизованная шина сигналов для всех ботов.

| Фича | Версия | Статус |
|-------|--------|--------|
| Signal broadcasting | v1.0 | ✅ |
| Multi-bot subscription | v1.0 | ✅ |

---

## 📊 HQ Dashboard

**Назначение**: Централизованный дашборд мониторинга всех ботов.

| Фича | Версия | Статус |
|-------|--------|--------|
| Web dashboard (Node.js) | v1.0 | ✅ |
| All bot status monitoring | v1.0 | ✅ |
| Trade history viewer | v1.0 | ✅ |

---

## 📝 Changelog (май 2026)

### 2026-05-28
- **Soldier**: Переключён на paper mode (Binance Testnet мёртв)
- **Soldier**: `fetch_pending_signals` — balanced long/short fetch (top 10+10)
- **Scalper Pro**: Signal query — balanced long/short + 24h freshness filter
- **Scalper Pro**: Stop recovery on PM2 restart (stops/sizer rebuilt)
- **Scalper Pro**: Clean state reset ($5,000)

### 2026-05-25
- **Scalper Pro**: Risk management v2.0 overhaul
  - Position cap 15%, exposure cap 100%, direction limit 3
  - Trail 0.8% (was 0.15%), emergency stop 3% (was 5%)
  - Symbol blacklist after 2 consecutive losses
- **Scalper Pro**: IIE v2 hypothesis engine — partial checkpoint analysis
- **Scalper Pro**: Hypothesis trail fix (50% of SL, min 0.5%)
- **Scalper Pro**: TG bot — /kill, /resume commands with inline keyboard
- **Scalper Pro**: TG bot — command menu registered via setMyCommands
