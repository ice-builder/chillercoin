# 📧 ZeroChainAI — Email Infrastructure Setup
**Домен:** `0chain.ai` | **Провайдер:** Zoho Mail Business

---

## Шаг 1: Регистрация Zoho Mail

- [ ] Открыть [mail.zoho.com/biz](https://mail.zoho.com/biz/) → выбрать **Business Email**
- [ ] Выбрать план: **Mail Lite** (~$1/мес/юзер) или **Workplace Standard** (~$3/мес)
- [ ] Ввести домен `0chain.ai` и начать верификацию

---

## Шаг 2: Верификация домена в Cloudflare DNS

- [ ] Зайти в Cloudflare → DNS → `0chain.ai`
- [ ] Добавить **TXT-запись верификации** (Zoho покажет значение):
  ```
  Тип: TXT
  Имя: @  (или zb-xxxxxxx.0chain.ai)
  Значение: zoho-verification=xxxxxxxx
  ```
- [ ] Нажать **Verify** в Zoho → дождаться подтверждения

---

## Шаг 3: Добавить MX записи (приём почты)

- [ ] В Cloudflare DNS добавить MX-записи:
  ```
  Тип: MX | Имя: @ | Приоритет: 10 | Значение: mx.zoho.eu
  Тип: MX | Имя: @ | Приоритет: 20 | Значение: mx2.zoho.eu
  Тип: MX | Имя: @ | Приоритет: 50 | Значение: mx3.zoho.eu
  ```

---

## Шаг 4: SPF, DKIM, DMARC (защита от спуфинга)

- [ ] **SPF** — добавить в Cloudflare:
  ```
  Тип: TXT | Имя: @ | Значение: "v=spf1 include:zoho.eu ~all"
  ```
- [ ] **DKIM** — в Zoho Mail: Settings → Email Authentication → DKIM → скопировать ключ → добавить в Cloudflare:
  ```
  Тип: TXT | Имя: zoho._domainkey | Значение: <ключ из Zoho>
  ```
- [ ] **DMARC** — добавить в Cloudflare:
  ```
  Тип: TXT | Имя: _dmarc | Значение: "v=DMARC1; p=quarantine; rua=mailto:admin@0chain.ai"
  ```

---

## Шаг 5: Создать почтовые ящики

| Ящик | Назначение | Приоритет |
|------|-----------|-----------|
| `contact@0chain.ai` | Клиенты, аудит-запросы, продажи | 🔴 Срочно |
| `security@0chain.ai` | Bug bounty, security disclosures | 🔴 Срочно |
| `infra@0chain.ai` | Все технические аккаунты (VPS, GitHub, Hetzner...) | 🔴 Срочно |
| `noreply@0chain.ai` | Авто-письма (ZeroScan отчёты) | 🟡 Потом |
| `admin@0chain.ai` | Vault, платёжки, критическая инфра | 🟡 Потом |

- [ ] Создать `contact@0chain.ai` → установить сильный пароль → включить 2FA
- [ ] Создать `security@0chain.ai` → включить 2FA
- [ ] Создать `infra@0chain.ai` → включить 2FA (только ты)
- [ ] Создать `noreply@0chain.ai` → только SMTP-отправка, вход заблокирован
- [ ] Создать `admin@0chain.ai` → максимальный пароль, 2FA hardware key

---

## Шаг 6: Привязать сервисы к infra@0chain.ai

- [ ] **Hetzner** — создать аккаунт + оплатить VPS CPX21 (~€8/мес)
- [ ] **GitHub Organization** → `ZeroChainAI` org
- [ ] **Cloudflare** — переключить аккаунт на `infra@` (если нужно)
- [ ] **Docker Hub** → org аккаунт
- [ ] **OpenAI** → platform.openai.com → API Key для o3
- [ ] **Anthropic** → привязать существующий ключ к `infra@`
- [ ] **Google AI** → привязать существующий ключ к `infra@`

---

## Шаг 7: Обновить лендинг и Telegram бот

- [ ] В `website/index.html` — заменить placeholder email на `contact@0chain.ai`
- [ ] В `bot/bot.py` — обновить `contact@0chain.ai` в тексте команд
- [ ] Проверить что `contact@` указан корректно во всех публичных местах

---

## Статус
> Последнее обновление: 05.05.2026
> Начало работ: когда вернёмся к проекту ZeroChainAI
