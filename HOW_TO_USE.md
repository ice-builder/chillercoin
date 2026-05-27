# How To Use The Crypto Research System

## Ежедневный рабочий цикл

### Шаг 1. Запусти среду

```bash
cd /Users/maxmg/Crypto-Code
source .venv/bin/activate
crypto-scalp research-app --data data/demo_btcusdt_1m.csv --artifacts artifacts/demo_run
```

Открой:

- [http://localhost:8501](http://localhost:8501)

## Шаг 2. Загрузи данные

В UI открой блок `Download Bybit candles`.

Выбери:

- `symbol`
- `interval`
- `start date`
- `end date`
- `start time (UTC)`
- `end time (UTC)`

Нажми:

- `Download from Bybit`

После этого выбери скачанный CSV в sidebar.

## Шаг 3. Исследуй рынок

Во вкладке `Market Research`:

1. Выбери CSV
2. При наличии модели выбери папку `artifacts`
3. Двигай окно истории
4. Смотри свечи
5. Смотри объем
6. Смотри признаки
7. Смотри вероятности модели

На что смотреть:

- всплески объема
- breakout локального high/low
- ускорение `ret_fast`
- рост volatility
- участки, где модель явно ошибается

## Шаг 4. Зафиксируй гипотезу

Пока добавление гипотез делается через CLI:

```bash
crypto-scalp hypothesis-add \
  --title "BTC impulse after breakout" \
  --thesis "После пробоя локального high и всплеска объема импульс часто продолжается 3-5 свечей" \
  --evidence "Чаще видно на BTCUSDT во время активной сессии" \
  --tags "btc,breakout,volume,impulse" \
  --symbol BTCUSDT \
  --timeframe 1m \
  --no-embed
```

После этого гипотеза появится во вкладке `Hypothesis Vault`.

## Шаг 5. Посмотри банк гипотез

Во вкладке `Hypothesis Vault` уже можно:

- видеть количество гипотез
- видеть текущую локальную модель
- фильтровать гипотезы по `symbol`
- фильтровать гипотезы по `timeframe`
- фильтровать гипотезы по `status`

## Шаг 6. Спроси локальную модель

Локальный synthesis можно вызвать через CLI:

```bash
crypto-scalp hypothesis-synthesize \
  --query "Какие сетапы по BTCUSDT 1m у нас уже есть и что проверить дальше?" \
  --symbol BTCUSDT \
  --timeframe 1m \
  --top-k 8 \
  --batch-size 4
```

Теперь это работает как memory pipeline:

- сначала retrieval выбирает самые релевантные гипотезы и summary memories
- потом модель делает промежуточные summaries по батчам
- и только после этого собирает финальный synthesis

То же самое уже доступно прямо во вкладке `Hypothesis Vault` в UI.

Сохраненные summaries можно посмотреть отдельно:

```bash
crypto-scalp summary-memory-list --symbol BTCUSDT --timeframe 1m
```

## Шаг 7. Обучи модель на новых данных

Если у тебя есть новый CSV:

```bash
crypto-scalp train \
  --data data/your_file.csv \
  --artifacts artifacts/your_run
```

Потом можно прогнать:

```bash
crypto-scalp backtest \
  --data data/your_file.csv \
  --artifacts artifacts/your_run
```

И снова открыть UI уже с этими артефактами.

## Практический режим работы

Самый удобный цикл такой:

1. Скачать свежие Bybit-свечи
2. Исследовать интересный участок на графике
3. Сформулировать гипотезу
4. Сохранить ее в vault
5. Накопить несколько гипотез
6. Запустить synthesis
7. Выбрать лучшую идею для следующего теста
8. Обучить или перепроверить модель на новом участке

## Что уже хорошо подходит для этой системы

- импульсный скальпинг
- breakout setup
- volume spike setup
- continuation after burst
- short-term futures research

## Что пока лучше не ждать

- полностью готового торгового бота
- автоторговли в один клик
- продвинутого execution engine
- production-grade risk management

Сейчас это именно сильная локальная research-среда.
