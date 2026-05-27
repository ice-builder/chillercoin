# 📚 Руководство пользователя — Crypto Futures Research & Trading Platform

> **Версия:** 3.0 (Апрель 2026)  
> **Архитектура:** Штаб (локально) + Солдат (VPS) + HQ Dashboard + Telegram Bot

---

## 🗺️ Архитектура системы

```
┌─────────────────────────────────────────────────────────────┐
│              ЛОКАЛЬНЫЙ КОМПЬЮТЕР (Mac mini M4)              │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │         🏛️ ШТАБ — Research App (Streamlit UI)        │  │
│  │                                                      │  │
│  │  📊 Dashboard       🔬 Market Research               │  │
│  │  ⚡ Impulse Lab     📝 Hypothesis Lab                │  │
│  │  🤖 Soldier Feedback 📜 Strategy History             │  │
│  │  💾 Data Lake                                        │  │
│  │                                                      │  │
│  │  PyTorch модель + Ollama LLM + SQLite гипотезы       │  │
│  └──────────────────────────────────────────────────────┘  │
│                          │ SSH Tunnel / SCP                 │
└──────────────────────────│──────────────────────────────────┘
                           │
┌──────────────────────────│──────────────────────────────────┐
│                 VPS (185.207.67.130:48113)                  │
│                                                             │
│  ┌──────────────────────────────────────────────────────┐  │
│  │           🪖 СОЛДАТ — Paper Trader (PM2)             │  │
│  │                                                      │  │
│  │  Каждые 60 секунд:                                   │  │
│  │  1. Сканирует Топ-20 горячих монет Bybit             │  │
│  │  2. Ищет импульсные сигналы (Volume Z-score)         │  │
│  │  3. Открывает/закрывает бумажные позиции             │  │
│  │  4. Шлёт уведомления в Telegram                      │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────┐  ┌──────────────────────────────┐ │
│  │ 📊 HQ Dashboard     │  │ 📱 Telegram HQ Bot          │ │
│  │ (порт 8585)         │  │ (scalper-hq-bot)             │ │
│  │                     │  │                              │ │
│  │ 7 страниц:          │  │ Команды:                     │ │
│  │ Dashboard, Trades,  │  │ /status /stop /resume        │ │
│  │ History, Analysis,  │  │ /analyze /history            │ │
│  │ Control, Position,  │  │ /rollback vN /closeall       │ │
│  │ Trade Detail        │  │                              │ │
│  └─────────────────────┘  └──────────────────────────────┘ │
│                                                             │
│  PM2: scalper-trader | scalper-monitor | scalper-hq-bot     │
└─────────────────────────────────────────────────────────────┘
```

---

## 1. 🚀 Быстрый старт

### Запуск локального Штаба (Streamlit)

```bash
cd /Users/maxmg/AG-projectd/Crypto-Code

# Стандартный запуск
./.venv/bin/crypto-scalp research-app --data data/demo_btcusdt_1m.csv

# Или через python напрямую
./.venv/bin/python3 -m crypto_scalp.cli research-app --data data/demo_btcusdt_1m.csv
```

Откройте: **http://localhost:8501**

### Доступ к VPS Dashboard (HQ Command)

```bash
# Открыть SSH-тоннель (выполнить на локальной машине)
ssh -i ~/.ssh/id_ed25519 -p 48113 -o StrictHostKeyChecking=no \
  -N -L 8585:127.0.0.1:8585 trader@185.207.67.130
```

Откройте: **http://localhost:8585**

### Требования

| Требование | Статус |
|---|---|
| Python 3.9+ | `.venv` уже настроен |
| Ollama (LLM) | `ollama serve` |
| Интернет | Для загрузки данных с Bybit |
| SSH-ключ | `~/.ssh/id_ed25519` (для VPS) |

---

## 2. 🏛️ Локальный Штаб (Streamlit UI)

Навигация осуществляется через **левую боковую панель**. Каждый раздел занимает полную ширину страницы.

### 📊 Dashboard — Главная страница

Верхняя часть — **Model Intelligence**:
- **Сигнал** — `LONG` / `SHORT` / `FLAT`
- **Confidence** — уверенность модели
- **Bias** — направленный перекос
- **Hit Rate** — точность на бэктесте
- **Return** — суммарная доходность

**Demo контур** — статистика бумажных сделок:

| Метрика | Описание |
|---|---|
| Баланс | Текущий баланс с учётом закрытых сделок |
| Win Rate | % прибыльных сделок |
| Сделок | Общее число + W/L |
| Long/Short PnL | PnL по направлениям |
| Equity Growth | График роста капитала |
| TP / BE / SL | Счётчики по причинам выхода |

**Sync VPS** — кнопка синхронизации данных с VPS-бота.

#### Как провалиться в сделку

1. Прокрутите до таблицы **«📋 Полный список сделок»**
2. Кликните чекбокс слева от строки
3. Откроется **Trade Inspector** — свечной график с маркерами входа/выхода

### 🔬 Market Research

1. Выберите монету и таймфрейм в Market Browser
2. Скачайте данные с Bybit или загрузите CSV
3. График: OHLC свечи + Volume + Z-score + вероятности модели

### ⚡ Impulse Lab

1. Выделите зону на графике мышью
2. Нажмите **Analyze Selection**
3. Просмотрите статистику похожих паттернов
4. **Strategy Builder** — бэктест с настройкой параметров

### 📝 Hypothesis Lab

- Добавляйте торговые идеи в SQLite-базу
- Фильтруйте по symbol, timeframe, status, score
- **Synthesis** — Ollama LLM анализирует гипотезы

### 🤖 Soldier Feedback

Управление VPS-ботом + анализ сделок:

1. **Синхронизировать данные** — скачать `paper_state_multi.json` с VPS
2. **Regret Analysis** — анализ упущенной выгоды по последним сделкам
3. **Рекомендации** — автоматические предложения по улучшению параметров
4. **Применить на VPS** — кнопка обновления параметров на сервере

### 📜 Strategy History

- **Version Overview** — карточки версий с PnL
- **Version Timeline** — развёрнутый changelog каждой версии
- **Live Performance Comparison** — таблица сравнения версий
- **All Trades by Version** — фильтр сделок по версии стратегии

### 💾 Data Lake

- Управление 3-летним Parquet-хранилищем данных
- Скачивание и обновление исторических данных
- Покрытие по инструментам и таймфреймам

---

## 3. 📊 VPS Dashboard (HQ Command)

Web-интерфейс на порту **8585** для удалённого мониторинга.

### Доступ

```bash
# SSH-тоннель (на локальной машине)
ssh -i ~/.ssh/id_ed25519 -p 48113 -N -L 8585:127.0.0.1:8585 trader@185.207.67.130

# Открыть в браузере
open http://localhost:8585
```

### 📊 Dashboard (/)

Главная страница с:
- **Метрики**: Status, Total PnL, Win Rate, Trades (W/L), Signals, Config Version
- **Active Positions**: таблица с **реальным uPnL** (получает цены из Bybit API) — кликните строку для перехода в детальный просмотр
- **Equity Curve**: визуализация роста PnL по сделкам
- **Recent Trades**: последние 5 закрытых сделок

### 📋 Trades (/trades)

Полная таблица всех сделок (включая архив v1):
- #, Symbol, Direction, Entry, Exit, PnL, Exit Reason, Strategy, Version, Time

### 📜 History (/history)

Версии стратегии:
- **Live Comparison** — таблица PnL/WR по версиям
- **Version Timeline** — развёрнутый changelog с параметрами каждой версии

### 🔬 Analysis (/analyze)

Глубокий анализ всех сделок:

**Верхние метрики:** Total PnL, Win Rate, Avg Win, Avg Loss, Profit Factor, Total Trades

**🧠 Strategy Cards** — для каждой стратегии:
| Метрика | Описание |
|---|---|
| Trades | Общее количество |
| Win Rate | % прибыльных |
| 🟢 Longs / 🔴 Shorts | Количество + WR по направлению |
| Long PnL / Short PnL | PnL по направлению |
| Profit Factor | Gross Profit / Gross Loss |
| Best / Worst Trade | Лучшая и худшая сделка |

**Breakdown Tables:**
- By Symbol — статистика по монетам (с L/S split)
- By Exit Reason — анализ причин выхода
- By Direction — сравнение Long vs Short
- By Version — сравнение версий стратегии

**📋 All Trades** — полная таблица сделок, **кликните на любую строку** для перехода в Trade Detail.

### 🔍 Trade Detail (/trade/N)

Детальный разбор конкретной сделки:
- **Метрики**: Result %, R Multiple, Exit Reason, Bars Held, Strategy
- **Price Ladder**: визуальная шкала с уровнями TP (🟢 пунктир), Entry (🔵 сплошная), Exit (🟠 сплошная), SL (🔴 сплошная)
- **TradingView 5m chart** — полноценный интерактивный график
- **Entry/Exit карточки** — все детали входа и выхода

### 📡 Position Detail (/position/SYMBOL)

Мониторинг открытой позиции в реальном времени:
- **Current Price** — live из Bybit API (кэш 10 сек)
- **Unrealized P/L** — расчёт в реальном времени
- **To Stop / To TP** — расстояние до уровней в %
- **Price Ladder** — живая шкала с пульсирующим индикатором NOW
- **TradingView chart** — 5m свечи
- **💀 CLOSE кнопка** — ручное закрытие с 7-секундным обратным отсчётом

> [!WARNING]
> Кнопка CLOSE записывает позицию как закрытую в state file. Бот перестанет её отслеживать. Используйте только при необходимости!

### ⚙️ Control (/control)

- Kill Switch: ON/OFF
- Текущие параметры стратегии
- Информация о боте
- Список Telegram-команд

---

## 4. 📱 Telegram Bot (HQ Bot)

### Команды

| Команда | Действие |
|---|---|
| `/status` | 📊 Текущий статус: PnL, WR, активные позиции |
| `/stop` | 🛑 Аварийная остановка (kill switch) |
| `/resume` | ▶️ Возобновить торговлю |
| `/analyze` | 🔬 Краткий анализ сделок |
| `/history` | 📜 История версий стратегии |
| `/rollback v1` | 🔄 Откатить стратегию на версию v1 |
| `/closeall` | 💀 Принудительно закрыть все позиции |

### Уведомления

**Новый сигнал:**
```
📡 New Signal Detected!
🟢 LONG SOLUSDT
Entry: 145.23 | SL: 144.73 | TP: 146.73
Risk: 0.34% | Reward: 1.03%
Active: 2/5
```

**Закрытие сделки:**
```
✅ Paper Trade #7
🔴 SHORT XRPUSDT
Entry: 0.5234 → Exit: 0.5201
PnL: +0.630% | Reason: take_profit
Session: W6/L1 | WR: 86% | Total PnL: +2.840%
```

### Настройка

Переменные окружения (`deploy_vps/.env`):
```env
TELEGRAM_SCALPER_BOT_TOKEN=<ваш токен>
TELEGRAM_CHAT_ID=<ваш chat_id>
```

---

## 5. 🪖 VPS-бот — Алгоритм работы

### Цикл (каждые 60 секунд)

```
1. HOT SYMBOLS DISCOVERY (каждый час)
   └─ Скачивает тикеры с Bybit
   └─ Фильтрует монеты с turnover24h > $5M
   └─ Считает Volume Z-Score (hourly)
   └─ Берёт TOP-20 самых горячих

2. SIGNAL DETECTION (каждую минуту, для каждого символа)
   └─ Скачивает последние 200 свечей (5m)
   └─ Считает Rolling Z-Score: объём и возврат цены
   └─ Импульс = dollar_volume_z >= 3.0 AND abs_ret_z >= 2.5
   └─ Подтверждение трендом: цена выше/ниже EMA-50
   └─ Ожидание подтяжки: entry = close - 50% размаха свечи

3. POSITION MANAGEMENT
   └─ Breakeven при достижении 1.0R (равен стопу в профите)
   └─ Take Profit = SL × 3.0 (R:R = 1:3)
   └─ Max bars held = 50 (time exit)

4. TELEGRAM УВЕДОМЛЕНИЯ
   └─ 📡 Новый сигнал (вход)
   └─ ✅/❌ Закрытие сделки (с PnL и win rate)
```

### Параметры стратегии (v2)

| Параметр | Значение | Описание |
|---|---|---|
| `lookback_bars` | 100 | Окно для Z-score |
| `min_dollar_volume_z` | 3.0 | Минимальный z-score объёма |
| `min_price_return_z` | 2.5 | Минимальный z-score движения |
| `fixed_stop_loss_pct` | 0.35% | Базовый стоп-лосс |
| `take_profit_rr` | 3.0 | Risk:Reward для TP |
| `entry_pullback_pct` | 0.5 | % подтяжки от размаха свечи |
| `trend_ema_period` | 50 | Период трендовой EMA |
| `breakeven_at_rr` | 1.0 | Активация breakeven (1R в профите) |
| `max_hold_bars` | 50 | Максимум баров в позиции |
| `account_risk_pct` | 0.10% | Риск от депозита на сделку |

### Управление через SSH

```bash
# Подключение
ssh -i ~/.ssh/id_ed25519 -p 48113 trader@185.207.67.130

# PM2 команды
pm2 list                                    # Статус процессов
pm2 logs scalper-trader --lines 50 --nostream  # Логи бота
pm2 logs scalper-monitor --lines 20 --nostream # Логи монитора
pm2 restart scalper-trader                  # Перезапуск бота
pm2 restart scalper-monitor                 # Перезапуск дашборда

# Проверка состояния
cat /home/trader/impulse-scalper/.local_ai/paper_trading/paper_state_multi.json | python3 -m json.tool
```

---

## 6. 📂 Структура файлов

```
Crypto-Code/
├── src/crypto_scalp/          # Штаб (локальный)
│   ├── research_app.py        # 🖥️ Streamlit UI (6000+ строк)
│   ├── confluence_strategy.py # 🧠 16-стратегий confluence движок
│   ├── vps_sync.py            # 🔗 Синхронизация с VPS
│   ├── model.py               # 🧠 PyTorch нейросеть
│   ├── strategy_engine.py     # ⚙️ Движок стратегий
│   ├── impulse_lab_ui.py      # ⚡ Impulse Lab UI
│   ├── bybit_live.py          # 📡 Загрузка данных Bybit
│   └── config.py              # ⚙️ Конфигурация
│
├── deploy_vps/                # Солдат (VPS)
│   ├── paper_trader.py        # 🪖 Основной трейдер
│   ├── monitor.py             # 📊 HQ Dashboard (порт 8585)
│   ├── hq_bot.py              # 📱 Telegram HQ Bot
│   ├── test_telegram.py       # 🔍 Тест связи
│   ├── setup.sh               # 🚀 Скрипт деплоя
│   └── .env                   # 🔑 Токены
│
├── data/                      # Данные
│   ├── demo_btcusdt_1m.csv    # Демо-данные
│   ├── vps_sync/              # Кэш данных с VPS
│   └── bybit/                 # Data Lake (Parquet)
│
├── .local_ai/                 # AI-хранилище
│   ├── config.json            # Настройки
│   ├── hypotheses.db          # SQLite гипотезы
│   └── paper_trading/         # Локальные paper-сделки
│
├── artifacts/                 # Модели и артефакты
├── USER_GUIDE.md              # 📚 Это руководство
├── HOW_TO_USE.md              # Краткая инструкция
└── README.md                  # Описание проекта
```

---

## 7. 🔄 Типичный рабочий процесс

### Ежедневная рутина

```
Утро:
1. Открыть SSH-тоннель: ssh -N -L 8585:... 
2. Открыть HQ Dashboard: http://localhost:8585
3. Проверить Active Positions — посмотреть uPnL
4. Провалиться в каждую позицию — оценить на графике

Середина дня:
5. Зайти в /analyze — проверить стратегию
6. Кликнуть сомнительные сделки — разобрать на графике
7. Открыть Streamlit Штаб → Soldier Feedback → Regret Analysis

Вечер:
8. Strategy History — посмотреть тренд по версии
9. Hypothesis Lab — записать выводы дня
10. Если нужно — /rollback или /stop через Telegram
```

### Петля оптимизации

```
VPS данные → Regret Analysis → Рекомендация → Новая версия → Мониторинг
     ▲                                                          │
     └────────────────── Следующий цикл ────────────────────────┘
```

---

## 8. 🐛 Решение проблем

### HQ Dashboard не открывается

```bash
# 1. Проверить что процесс живой
ssh -p 48113 trader@185.207.67.130 "pm2 list | grep scalper-monitor"

# 2. Если errored — проверить порт
ssh -p 48113 trader@185.207.67.130 "fuser -k 8585/tcp; pm2 restart scalper-monitor"

# 3. Переподключить тоннель
lsof -ti:8585 | xargs kill -9
ssh -N -L 8585:127.0.0.1:8585 -p 48113 trader@185.207.67.130
```

### Бот не шлёт в Telegram

```bash
# Тест подключения
ssh -p 48113 trader@185.207.67.130 "cd /home/trader/impulse-scalper && venv/bin/python test_telegram.py"

# Логи на ошибки
pm2 logs scalper-hq-bot --lines 30 --nostream
```

### Trade Inspector показывает пустой график

- Используйте свежие сделки — старые данные Bybit могут быть недоступны
- Проверьте подключение к интернету

### Streamlit не запускается

```bash
# Проверить зависимости
.venv/bin/pip install -e ".[dev]"

# Проверить синтаксис
.venv/bin/python3 -m py_compile src/crypto_scalp/research_app.py && echo OK
```

---

## 9. ⚙️ Продвинутые настройки

### Ручное обновление параметров

```bash
# Через SSH
ssh -p 48113 trader@185.207.67.130 \
  "echo '{\"take_profit_rr\": 3.5}' > \
  /home/trader/impulse-scalper/.local_ai/paper_trading/optimized_params.json"
```

### Деплой обновлений кода

```bash
# Отправить обновлённый бот
scp -P 48113 deploy_vps/paper_trader.py trader@185.207.67.130:/home/trader/impulse-scalper/

# Отправить обновлённый дашборд  
scp -P 48113 deploy_vps/monitor.py trader@185.207.67.130:/home/trader/impulse-scalper/

# Перезапустить
ssh -p 48113 trader@185.207.67.130 "pm2 restart scalper-trader scalper-monitor"
```

### Добавление новой версии стратегии

Используйте Telegram: `/rollback vN` или обновите `strategy_history.json` на VPS.

---

*Последнее обновление: 29 апреля 2026 | Crypto-Code v3.0*
