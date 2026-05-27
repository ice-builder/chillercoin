# 🎯 HQ Dashboard — Manual

## Быстрый старт (SSH Tunnel с VPS)

Dashboard уже запущен на VPS через PM2. Для доступа нужен SSH туннель:

```bash
# Подключение к VPS с пробросом порта
ssh -p 48113 -L 8588:localhost:8588 -N trader@185.207.67.130
```

Открыть: **http://localhost:8588**

> ⚠️ Флаг `-N` — без shell (только туннель). Убрать `-N` если нужен доступ к shell VPS.

## Альтернатива: SSH + Shell

```bash
# С доступом к шеллу VPS (для управления pm2, логами и т.д.)
ssh -p 48113 -L 8588:localhost:8588 trader@185.207.67.130
```

## PM2 команды (на VPS)

```bash
# Статус всех процессов
pm2 list

# Логи дашборда
pm2 logs hq-dashboard

# Перезапуск дашборда
pm2 restart hq-dashboard

# Логи Солдата
pm2 logs soldier-trader

# Логи Pump Hunter
pm2 logs pump-hunter
```

## Страницы

| URL | Описание |
|-----|----------|
| `/` | 🏠 HQ Overview — оба бота рядом |
| `/scalper` | ⚔️ Soldier Dashboard |
| `/scalper/trades` | 📋 История сделок скальпера |
| `/scalper/history` | 📜 Версии стратегий |
| `/scalper/analyze` | 🔬 Анализ трейдов |
| `/scalper/control` | ⚙️ Kill switch, параметры |
| `/scalper/position/SYMBOL` | 📡 Детали позиции + TradingView |
| `/pumps` | 🎯 Pump Hunter Dashboard |
| `/pumps/trades` | 📋 Pump Trades |
| `/pumps/analyze` | 🔬 Pump Analysis |
| `/pumps/position/KEY` | 📡 Pump позиция + TradingView |
| `/exchange/positions` | 📍 Позиции на бирже |
| `/exchange/history` | 📜 История сделок биржи |
| `/exchange/equity` | 📈 Кривая PnL |

## VPS Информация

| Параметр | Значение |
|----------|----------|
| **Хост** | `185.207.67.130` |
| **SSH порт** | `48113` |
| **Юзер** | `trader` |
| **Dashboard порт** | `8588` |
| **CWD** | `/home/trader/soldier` |

## Файлы данных (на VPS)

| Бот | Путь к стейту |
|-----|---------------|
| Soldier | `/home/trader/soldier/.local_ai/paper_trading/paper_state_multi.json` |
| Pump Hunter | `/home/trader/pump_hunter/demo_state.json` |

## Остановка туннеля

`Ctrl+C` в терминале с SSH туннелем

## Troubleshooting

| Проблема | Решение |
|----------|---------|
| Дашборд не грузится | `pm2 restart hq-dashboard` на VPS |
| Порт занят | `lsof -ti :8588 \| xargs kill` локально |
| SSH refused | Проверить ключ: `ssh -p 48113 trader@185.207.67.130` |
| Пустые данные | Проверить PM2: `pm2 list` (soldier-trader/pump-hunter online?) |
