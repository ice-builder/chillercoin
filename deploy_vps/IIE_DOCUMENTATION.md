# 🧠 IIE — Impulse Intelligence Engine
## Полная операционная документация

> **VPS**: `185.207.67.130` · **SSH**: порт `48113` · **User**: `trader`
> **OS**: Ubuntu 22.04 (6.8.0) · **RAM**: 8 GB (используется ~1.3 GB) · **Python**: venv

---

## 1. Общая архитектура

```
                        ┌─────────────────────────────────────────────┐
                        │           VPS — 185.207.67.130              │
                        │                                             │
                        │  ┌─────────┐  ┌─────────┐  ┌────────────┐  │
                        │  │ Soldier │  │ Pump    │  │ Insider    │  │
                        │  │ (demo)  │  │ Hunter  │  │ Scanner    │  │
                        │  └────┬────┘  └────┬────┘  └─────┬──────┘  │
                        │       │            │             │          │
                        │       ▼            ▼             ▼          │
                        │  ┌──────────────────────────────────────┐   │
                        │  │    🧠 IIE Engine (iie_daemon.py)     │   │
                        │  │                                      │   │
                        │  │  ⚡ Collector → 📊 Tracker →         │   │
                        │  │  🧭 Phase → 🪙 Scorer →              │   │
                        │  │  🧠 ML Predictor → 🎛️ Adaptive Mgr  │   │
                        │  └───────────────┬──────────────────────┘   │
                        │                  │                          │
                        │         ┌────────┴────────┐                 │
                        │         │  SQLite DB      │                 │
                        │         │  impulses.db    │                 │
                        │         └────────┬────────┘                 │
                        │                  │                          │
                        │  ┌───────────────┴──────────────────────┐   │
                        │  │  📊 HQ Dashboard (monitor.py :8588)  │   │
                        │  │  + iie_pages.py (IIE секция)         │   │
                        │  └──────────────────────────────────────┘   │
                        │                  │                          │
                        │         ┌────────┴────────┐                 │
                        │         │   Telegram API  │                 │
                        │         │   (отчёты 6ч)   │                 │
                        │         └─────────────────┘                 │
                        └─────────────────────────────────────────────┘
```

---

## 2. Все процессы (PM2)

| # | Имя | Скрипт | CWD | Порт | Описание |
|---|-----|--------|-----|------|----------|
| 0 | `soldier-trader` | `paper_trader.py --top 15 --max-pos 5 --interval 60` | `/home/trader/soldier` | — | Импульсный скальпер, торгует на Binance Testnet |
| 2 | `hq-dashboard` | `monitor.py --port 8588` | `/home/trader/soldier` | **8588** | HQ штаб — все боты + IIE |
| 3 | `oracle-bot` | `tg_status_bot.py` | `/home/trader/soldier` | — | Telegram бот для статусов |
| 4 | `scalper-bot` | `telegram_command_bot.py` | `/home/trader` | — | Telegram бот для команд |
| 7 | `pump-hunter` | `pump_scanner_v2.py` | `/home/trader/pump-hunter` | — | Сканер памп-сигналов (Bybit) |
| 8 | `insider-scanner` | `insider_scanner.py` | `/home/trader/insider-scanner` | — | Инсайдер-анализ OI аномалий |
| 9 | `iie-engine` | `python -m iie.iie_daemon` | `/home/trader/soldier` | — | **IIE — мозг системы** |

---

## 3. Как всё запустить

### 3.1 Подключение к VPS

```bash
# SSH в терминал
ssh -p 48113 trader@185.207.67.130

# SSH туннель для дашборда (отдельный терминал)
ssh -p 48113 -N -L 8588:127.0.0.1:8588 trader@185.207.67.130
```

После туннеля дашборд доступен: **http://127.0.0.1:8588**

### 3.2 Управление процессами

```bash
# Статус всех процессов
pm2 list

# Запустить все
pm2 start all

# Остановить все
pm2 stop all

# Перезапустить конкретный
pm2 restart iie-engine
pm2 restart hq-dashboard
pm2 restart soldier-trader
pm2 restart pump-hunter
pm2 restart insider-scanner

# Логи (live)
pm2 logs iie-engine
pm2 logs soldier-trader

# Логи (последние N строк)
pm2 logs iie-engine --nostream --lines 30

# Сохранить конфигурацию PM2 (чтобы процессы восстанавливались после reboot)
pm2 save
```

### 3.3 Первый запуск IIE (если с нуля)

```bash
cd /home/trader/soldier

# Установить зависимости
venv/bin/pip install pandas numpy requests scikit-learn xgboost python-dotenv

# Запустить
pm2 start venv/bin/python --name iie-engine -- -m iie.iie_daemon

# Проверить
pm2 logs iie-engine --nostream --lines 10
```

При первом запуске IIE автоматически:
1. Создаёт SQLite базу в `iie/data/impulses.db`
2. Импортирует историю сделок из Soldier и Pump Hunter
3. Начинает сканирование импульсов

---

## 4. Как работает IIE — слой за слоем

### Общий цикл работы

```
Каждые 5 мин  → Impulse Collector сканирует 200 монет
Каждые 15 мин → Post-Trade Tracker обновляет исходы
Каждые 1 час  → Coin Scorer пересчитывает профили монет
Каждые 4 часа → Market Phase определяет макро-фазу рынка
Каждые 6 часов → TG Report отправляется в Telegram
Каждые 24 часа → ML Predictor переобучается на новых данных
```

---

### 4.1 ⚡ Impulse Collector (`impulse_collector.py`)

**Что делает:** Сканирует топ-200 монет на Bybit каждые 5 минут. Ищет аномальные объёмы и движения цены.

**Как работает:**
1. Получает список монет отсортированных по 24h обороту
2. Для каждой монеты загружает свечи (5m, 15m, 1h)
3. Считает Z-score объёма и возврата по скользящему окну
4. Если `vol_z >= 3.0` И `ret_z >= 2.0` → записывает импульс в БД

**Ключевые параметры:**

| Параметр | Значение | Описание |
|----------|----------|----------|
| `COLLECTOR_INTERVAL_SEC` | 300 (5 мин) | Интервал сканирования |
| `COLLECTOR_TOP_COINS` | 200 | Сколько монет сканировать |
| `COLLECTOR_MIN_TURNOVER_24H` | 500,000 | Минимальный оборот ($) |
| `IMPULSE_MIN_VOL_Z` | 3.0 | Порог Z-score объёма |
| `IMPULSE_MIN_RET_Z` | 2.0 | Порог Z-score ретурна |

**Данные, которые сохраняются по каждому импульсу:**
- Символ, направление (long/short), таймфрейм
- `vol_z`, `ret_z`, `combined_score` (vol_z * ret_z)
- RSI, отклонение от EMA, ATR
- Процент тела свечи, соотношение фитилей
- Положение (at_high / at_low / mid_range)

---

### 4.2 📊 Post-Trade Tracker (`post_trade_tracker.py`)

**Что делает:** Отслеживает что происходит с ценой ПОСЛЕ каждого импульса. Это ключевые данные для самообучения.

**Как работает:**
1. Берёт все импульсы без завершённого tracking
2. Для каждого проверяет текущую цену через API
3. Обновляет checkpoints: 5m, 15m, 1h, 4h, 24h, 48h, 7d
4. Записывает max favorable (максимально в нашу сторону) и max adverse (максимально против)
5. Определяет stop hunt (если цена развернулась на 50%+ за 3 бара)

**Параметры:**

| Параметр | Значение | Описание |
|----------|----------|----------|
| `POST_TRACKER_INTERVAL_SEC` | 900 (15 мин) | Интервал обновления |
| `POST_TRACKER_MAX_AGE_SEC` | 604800 (7 дней) | Макс. время отслеживания |
| `STOP_HUNT_REVERSAL_PCT` | 50% | Порог разворота для stop hunt |
| `STOP_HUNT_MAX_BARS` | 3 | Окно для определения stop hunt |

**Что узнаём:**
- Средний favorable move после импульса
- Средний adverse move (просадка)
- Процент stop hunt'ов (ложных пробоев)
- Продолжение тренда или разворот

---

### 4.3 🧭 Market Phase Detector (`market_phase.py`)

**Что делает:** Определяет текущую макро-фазу рынка каждые 4 часа.

**Фазы:**

| Фаза | Условие | Влияние |
|------|---------|---------|
| `trending_up` | BTC +10%+ за месяц, EMA20 > EMA50 | Long-сигналы усилены |
| `trending_down` | BTC -10%+ за месяц, EMA20 < EMA50 | Short-сигналы усилены |
| `sideways` | BTC +-10%, низкая ATR | Нейтрально |
| `volatile` | Высокая ATR, расхождение EMA | Позиция уменьшена |

**Данные для определения:**
- BTC цена и месячное изменение
- ETH цена и месячное изменение
- EMA20 / EMA50 на 4h BTC
- ATR (14) на 4h BTC
- Корреляция альтов с BTC

**Параметры:**

| Параметр | Значение | Описание |
|----------|----------|----------|
| `MARKET_PHASE_INTERVAL_SEC` | 14400 (4 часа) | Интервал определения |
| `MARKET_PHASE_TRENDING_THRESHOLD` | 10.0% | Порог для trending |
| `MARKET_PHASE_EMA_FAST` | 20 | Быстрая EMA |
| `MARKET_PHASE_EMA_SLOW` | 50 | Медленная EMA |

---

### 4.4 🪙 Coin Scorer (`coin_scorer.py`)

**Что делает:** Строит "профиль личности" каждой монеты на основе её исторических импульсов.

**Метрики профиля:**

| Метрика | Что означает |
|---------|-------------|
| `impulse_quality_score` (0-100) | Насколько надёжны импульсы этой монеты |
| `momentum_persistence` (0-100) | Продолжается ли тренд после импульса |
| `stop_hunt_frequency` (0-100%) | Как часто импульсы — ложные пробои |
| `predictability_score` (0-100) | Насколько предсказуемо поведение |
| `volatility_regime` | high / medium / low |
| `recommended_sl_mult` | Множитель стоп-лосса (0.5x-2.0x) |
| `recommended_hold_bars` | Оптимальное время удержания |
| `best_tf` | Лучший таймфрейм для входа |

**Параметры:**

| Параметр | Значение | Описание |
|----------|----------|----------|
| `COIN_SCORER_INTERVAL_SEC` | 3600 (1 час) | Интервал пересчёта |
| `COIN_SCORER_MIN_IMPULSES` | 10 | Мин. импульсов для профиля |

> Профили появятся когда по монете будет 10+ **завершённых** outcome (через ~7 дней работы)

---

### 4.5 🧠 ML Predictor (`impulse_predictor.py`)

**Что делает:** XGBoost модель, которая предсказывает исход импульса ДО входа.

**3 модели:**

| Модель | Тип | Что предсказывает |
|--------|-----|-------------------|
| `will_continue` | Классификация | Продолжится ли движение (0/1) |
| `max_favorable_pct` | Регрессия | Ожидаемый максимальный профит (%) |
| `is_stop_hunt` | Классификация | Ложный пробой или нет (0/1) |

**19 входных фич:**
- Z-scores (vol, ret, combined)
- RSI, EMA deviation, ATR
- Candle body %, wick ratios
- Direction, impulse location (one-hot)
- Hour (sin/cos encoding)
- Coin profile metrics (quality, momentum, stop_hunts, level_respect)

**Параметры:**

| Параметр | Значение | Описание |
|----------|----------|----------|
| `PREDICTOR_RETRAIN_INTERVAL_SEC` | 86400 (24 часа) | Интервал переобучения |
| `PREDICTOR_MIN_SAMPLES` | 100 | Мин. outcomes для первого обучения |

> ML модель начнёт тренировку когда накопится 100+ **completed** outcomes. При текущем темпе это ~1-2 недели.

---

### 4.6 🎛️ Adaptive Position Manager (`adaptive_manager.py`)

**Что делает:** Центральный движок решений. Объединяет все слои и выдаёт рекомендацию.

**Что возвращает `TradeRecommendation`:**

```
should_enter: bool        — входить или нет
confidence: 0-100         — уверенность
score: 0-100              — композитный скор
reason: str               — объяснение

recommended_sl_pct        — адаптивный стоп-лосс
recommended_tp_pct        — адаптивный тейк-профит
recommended_hold_bars     — сколько держать
recommended_trail_pct     — трейлинг стоп

position_size_mult        — множитель позиции (0.5x-2.0x)
position_size_reason      — объяснение размера

will_continue_prob        — ML вероятность продолжения
predicted_favorable_pct   — ML предсказание профита
stop_hunt_prob            — ML вероятность stop hunt
```

**Логика принятия решений:**

```
Score >= 60  → "strong signal" — входим
Score 40-60  → "moderate signal" — входим с осторожностью
Score < 40   → "weak" — пропускаем

Overrides:
- stop_hunt_prob > 70% → БЛОК
- coin quality < 20 (при 20+ импульсах) → БЛОК
```

**Адаптивный размер позиции:**

| Фактор | Влияние |
|--------|---------|
| ML confidence > 70% | x1.3 |
| ML confidence < 35% | x0.6 |
| Coin quality > 75 | x1.3 |
| Coin quality < 30 | x0.6 |
| Stop hunt freq > 50% | x0.7 |
| Volatile market | x0.7 |
| Итого | **0.5x — 2.0x** |

---

## 5. HQ Dashboard — Страницы

### Доступ

```bash
# Открыть SSH туннель (в отдельном терминале)
ssh -p 48113 -N -L 8588:127.0.0.1:8588 trader@185.207.67.130

# Открыть в браузере
open http://127.0.0.1:8588
```

### Навигация

| Секция | Страница | URL | Описание |
|--------|----------|-----|----------|
| **HQ** | Overview | `/` | Общий обзор всех ботов |
| **Soldier** | Dashboard | `/scalper` | Позиции, PnL, equity |
| | Trades | `/scalper/trades` | История сделок |
| | History | `/scalper/history` | Версии стратегий |
| | Analysis | `/scalper/analyze` | Разбор по символам/стратегиям |
| | Control | `/scalper/control` | Управление (kill switch) |
| **Pump Hunter** | Dashboard | `/pumps` | Активные позиции, PnL |
| | Trades | `/pumps/trades` | История памп-сделок |
| | Analysis | `/pumps/analyze` | Аналитика |
| **Insider** | Dashboard | `/insider` | OI аномалии |
| | Signals | `/insider/signals` | Live сигналы |
| **IIE Engine** | Overview | `/iie` | **Полный обзор движка** |
| | Impulses | `/iie/impulses` | **Live лента импульсов** |
| | Coins | `/iie/coins` | **Профили монет** |
| | Config | `/iie/config` | **Редактор параметров** |
| **Exchange** | Positions | `/exchange/positions` | Реальные позиции на бирже |
| | History | `/exchange/history` | История по дням |
| | Equity | `/exchange/equity` | Кривая PnL |

### Config Editor (`/iie/config`)

Позволяет менять **все параметры IIE в реальном времени** без перезапуска:

1. Открой `/iie/config`
2. Измени значение любого параметра
3. Нажми **Save Changes**
4. Изменения применяются на следующем цикле

---

## 6. Telegram отчёты

IIE автоматически отправляет статус каждые **6 часов** в Telegram.

**Ручная отправка:**

```bash
# С VPS
cd /home/trader/soldier
venv/bin/python -m iie.report --telegram

# Через SSH
ssh -p 48113 trader@185.207.67.130 "cd /home/trader/soldier && venv/bin/python -m iie.report --telegram"
```

**Формат отчёта:**
```
🧠 IIE STATUS REPORT
━━━━━━━━━━━━━━━━━━━━━━━━
📦 DB: 215 impulses | 33 trades
📈 Phase: TRENDING_UP
₿ BTC: 80,339 (+16.4% mo) | Alt corr: 0.67

⚡ Impulses: 87 (1h) / 100 (24h)
   Long: 53 | Short: 47

🏆 Top Impulses:
  🟢 ACEUSDT [15] score=47.4
  🟢 MYXUSDT [60] score=16.7
  🔴 DYMUSDT [60] score=15.7

🤖 Bots:
  ❌ pump_hunter: 12 | WR 17% | -23.150%
  ❌ soldier: 21 | WR 24% | -5.931%
```

---

## 7. Структура файлов на VPS

```
/home/trader/soldier/
├── paper_trader.py          # Soldier bot
├── monitor.py               # HQ Dashboard
├── exchange_executor.py     # Binance API executor
├── position_registry.py     # Position tracking
├── tg_status_bot.py         # Oracle TG bot
├── telegram_command_bot.py  # Command TG bot
├── iie_pages.py             # IIE dashboard pages
├── insider_pages.py         # Insider dashboard pages
├── .env                     # API keys, tokens
│
├── iie/                     # 🧠 Impulse Intelligence Engine
│   ├── __init__.py
│   ├── config.py            # Все параметры IIE
│   ├── impulse_db.py        # SQLite база (5 таблиц)
│   ├── impulse_collector.py # Сканер импульсов
│   ├── post_trade_tracker.py# Трекер исходов
│   ├── market_phase.py      # Определение фазы рынка
│   ├── coin_scorer.py       # Скоринг монет
│   ├── impulse_predictor.py # ML модель (XGBoost)
│   ├── adaptive_manager.py  # Адаптивный менеджер позиций
│   ├── report.py            # Генератор отчётов
│   ├── iie_daemon.py        # Оркестратор (main loop)
│   ├── data/
│   │   ├── impulses.db      # SQLite database (WAL mode)
│   │   └── models/
│   │       └── predictor_state.pkl  # ML модели (после обучения)
│   └── tests/
│       └── test_db.py       # Smoke tests
│
├── .local_ai/paper_trading/ # Soldier state files
│   ├── paper_state_multi.json
│   └── strategy_history.json
│
└── venv/                    # Python virtual environment

/home/trader/pump-hunter/
├── pump_scanner_v2.py       # Pump Hunter bot
├── demo_state.json          # Pump Hunter state
└── ...

/home/trader/insider-scanner/
├── insider_scanner.py       # Insider Scanner
├── insider_state.json
├── oi_history.json
└── ...
```

---

## 8. База данных IIE

SQLite в WAL mode (concurrent reads). 5 таблиц:

| Таблица | Записей | Описание |
|---------|---------|----------|
| `impulses` | 215+ | Обнаруженные импульсы |
| `post_impulse_outcomes` | 215+ | Что произошло после импульса |
| `coin_profiles` | 0* | Профили монет |
| `market_phases` | 4 | История фаз рынка |
| `trade_outcomes` | 33 | Импортированные сделки ботов |

> *Профили появятся через 7+ дней когда outcomes будут completed

**Просмотр БД:**

```bash
# На VPS
cd /home/trader/soldier
venv/bin/python -c "from iie.impulse_db import ImpulseDB; print(ImpulseDB().stats())"

# Полный отчёт в терминал
venv/bin/python -m iie.report

# JSON export
venv/bin/python -m iie.report --json
```

---

## 9. Мониторинг и диагностика

### Быстрая проверка здоровья

```bash
# Все процессы живы?
pm2 list

# IIE daemon работает?
pm2 logs iie-engine --nostream --lines 5

# Dashboard доступен?
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8588/iie

# DB растёт?
venv/bin/python -c "from iie.impulse_db import ImpulseDB; print(ImpulseDB().stats())"

# Сколько RAM?
free -h | head -2
```

### Полезные команды

```bash
# Перезапустить всё
pm2 restart all

# Очистить логи
pm2 flush

# Мониторинг в реальном времени
pm2 monit

# Увидеть env переменные процесса
pm2 env 9  # 9 = iie-engine

# Сохранить для autostart после reboot
pm2 save
pm2 startup  # и выполнить выданную команду
```

---

## 10. Текущий статус (9 мая 2026)

| Компонент | Статус | Заметки |
|-----------|--------|---------|
| IIE Daemon | ✅ Работает | 215 импульсов собрано |
| Impulse Collector | ✅ Каждые 5 мин | 200 монет, 3 таймфрейма |
| Post-Trade Tracker | ✅ Каждые 15 мин | 215 outcomes tracking |
| Market Phase | ✅ Каждые 4ч | TRENDING_UP, BTC $80,339 |
| Coin Scorer | ✅ Каждые 1ч | Ждёт completed outcomes |
| ML Predictor | ⏳ Ждёт 100 samples | Нужно ~1-2 недели данных |
| TG Reports | ✅ Каждые 6ч | HTML формат |
| HQ Dashboard | ✅ Порт 8588 | Все 4 IIE страницы |
| Config Editor | ✅ Работает | Runtime changes |

### Что будет дальше (Phase 6)

Интеграция IIE в каждого бота:
1. **Soldier**: перед входом → `iie.evaluate_signal()` → адаптивный SL/TP/size
2. **Pump Hunter**: при сигнале → IIE score + coin quality
3. **Insider Scanner**: при OI аномалии → coin profile bonus

Это замкнёт feedback loop: боты используют IIE для решений → записывают результаты → IIE обучается → даёт лучшие решения.

---

## 11. Troubleshooting

| Проблема | Решение |
|----------|---------|
| Dashboard не открывается | Проверь SSH туннель: `ssh -p 48113 -N -L 8588:127.0.0.1:8588 trader@185.207.67.130` |
| Dashboard зависает при старте | Binance rate limit — `pm2 restart hq-dashboard`, подождать 30 сек |
| TG отчёт не приходит | `venv/bin/python -m iie.report --telegram` — смотреть ответ |
| IIE не собирает импульсы | `pm2 logs iie-engine --lines 20` — проверить ошибки API |
| Нет coin profiles | Нужно 10+ completed outcomes (7 дней) |
| ML не тренируется | Нужно 100+ completed outcomes (~2 недели) |
| PM2 процессы не восстанавливаются | `pm2 save` + `pm2 startup` |
| Мало RAM | Сейчас 1.3/8 GB — запас большой |
