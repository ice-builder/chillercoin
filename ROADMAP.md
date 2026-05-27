# Crypto Research System Roadmap

## Stage 1: Research UI Foundation

Статус: сделано

- локальный Streamlit UI
- загрузка своих CSV
- загрузка свечей Bybit
- исследование свечей и признаков
- просмотр train/backtest результатов
- вкладка `Hypothesis Vault`

## Stage 2: Interactive Hypothesis Vault

Статус: в работе

Цель:

- перенести работу с гипотезами из CLI в UI

Задачи:

1. Добавить форму создания гипотез в `Hypothesis Vault`
2. Добавить редактирование статуса гипотез
3. Добавить поиск по тегам, символу и таймфрейму
4. Добавить кнопку локального synthesis в UI
5. Добавить быстрые шаблоны гипотез

## Stage 3: Better Local AI Layer

Статус: частично готово

Цель:

- сделать локальную LLM полноценным research-ассистентом

Задачи:

1. Докачать `qwen3:4b` как основной reasoning-модуль
2. Подключить `embeddinggemma` для semantic search
3. Сделать retrieval по гипотезам через embeddings
4. Добавить summary по символу и таймфрейму
5. Добавить synthesis по конкретному рыночному окну

## Stage 4: Experiment Tracking

Статус: не начато

Цель:

- привязать идеи к проверкам и результатам

Задачи:

1. Добавить сущность `experiment`
2. Связать гипотезы с backtest-run
3. Хранить параметры эксперимента
4. Сохранять результат проверки гипотезы
5. Отмечать гипотезы как `confirmed`, `rejected`, `needs more data`

## Stage 5: Better Market Intelligence

Статус: не начато

Цель:

- усилить исследовательскую часть по рынку

Задачи:

1. Ресемплинг таймфреймов внутри UI
2. Multi-timeframe analysis
3. Отметки торговых сессий
4. Просмотр локальных экстремумов и breakouts
5. Быстрые фильтры по volatility regime

## Stage 6: Better Modeling

Статус: базово готово

Цель:

- улучшить качество модели и исследовательскую ценность сигналов

Задачи:

1. Добавить несколько моделей вместо одной
2. Сравнение моделей в UI
3. Более аккуратная time-series validation
4. Раздельные long/short quality metrics
5. Feature importance / ablation

## Stage 7: Real-Time Layer

Статус: не начато

Цель:

- перейти от оффлайн research к живому наблюдению

Задачи:

1. Live feed по Bybit
2. Потоковое обновление свечей
3. Live scoring модели
4. Paper trading
5. Журнал live-сигналов

## Stage 8: Execution Layer

Статус: не начато

Цель:

- перейти к полуавтоматической или автоматической торговле

Задачи:

1. Order management
2. Risk manager
3. Stops and position sizing
4. Exchange adapter
5. Safety checks and kill switch

## Near-Term Priority

Самый правильный следующий порядок:

1. Форма добавления гипотез в UI
2. Кнопка synthesis в UI
3. Semantic search через embeddings
4. Привязка гипотез к экспериментам
5. Сравнение моделей и сетапов по символам
