# Chiller Coin — граница Public / Private

Дата: 12.08.2026  
Принцип: **продукт и сайт — публично; торговая система и секреты — приватно.**

Публичный репозиторий: `chillercoin-public` → `github.com/ice-builder/chillercoin`  
Приватное: `Crypto-Code` (и VPS), без зеркалирования trading-слоя.

---

## PUBLIC (можно и нужно показывать)

### Сайт и продукт
| Путь / артефакт | Почему public |
|-----------------|---------------|
| `chiller-landing/` | Маркетинг, механика «just chill», без yield-promise |
| `chiller-dashboard/` | UX холдера: connect, deposit/withdraw UI, NAV view |
| Brand assets (logo, banner, mascot) | Доверие к бренду |
| README / LICENSE / whitepaper (docs.html) | Trust story |
| Program id + Solscan links | Верификация on-chain |

### On-chain vault (исходники контракта)
| Путь | Почему public |
|------|---------------|
| `programs/chiller-vault/src/lib.rs` (+ errors/events если используются) | Инвесторы сверяют mint/burn/pause/limits |
| `Anchor.toml`, `Cargo.toml`, `package.json`, `tsconfig.json` | Воспроизводимая сборка |
| `tests/` (без секретов) | Доказательство поведения |
| `deploy-devnet.sh` (без ключей) | Прозрачность деплоя |
| `docs/` compliance **спеки без ключей** (attestation/refund, schema SQL, sequences) | Показывает зрелость процесса |

### Что можно публиковать как «прозрачность», но не код торговли
- Публичный NAV snapshot / attestation (цифры, не ключи)
- Список on-chain `log_trade` событий (если уже в программе)
- Высокоуровневое описание: «sleeve на CEX через entity, KYT на входе»
- **Не** стратегии, **не** сигналы, **не** API биржи

---

## PRIVATE (никогда в public git / public CI artifacts)

### Торговая система
| Путь / слой | Почему private |
|-------------|----------------|
| Soldier / Insider / Scalper / LH / zoo | Альфа + инфраструктура |
| `deploy_vps/`, `src/`, `.local_ai/`, `artifacts/` | Движок, модели, деплой |
| `vault_bridge.py` | Мост ботов ↔ vault |
| `drift_executor.py`, `exchange_adapter.py`, любые `*_mirror.py` | Исполнение |
| Bybit / Drift / Jupiter / HL keys, sub-account ops | Доступ к деньгам |
| IIE / predictors / retrain scripts | Edge |
| TG ops / shop bots, sticker packs, bot tokens | Операционка + не trust-surface |

### Compliance ops (логика ок в доках; секреты — нет)
| Что | Public? |
|-----|---------|
| Описание KYT + attestation + refund SM | ✅ docs |
| Provider API keys, webhook secrets | ❌ |
| Quarantine hot-wallet private keys | ❌ |
| Attestation signer private key | ❌ |
| Internal `reason_code` / raw KYT payloads с PII | ❌ |
| Ops console с ручным unban | ❌ (или отдельный private repo) |

### Секреты и среда
| Что | |
|-----|--|
| `.env`, `*.db` с юзерами, `trade_log.jsonl` с внутренними метками | ❌ |
| SSH keys, pm2 dump с токенами | ❌ |
| Git remotes с embedded PAT/паролем | ❌ и **ротировать** если уже светились |

---

## СЕРАЯ ЗОНА — явное решение

| Тема | Рекомендация |
|------|----------------|
| **TG shop / любой Telegram bot** (`chiller-tg-bot`, токены, sticker shop) | **Private — не пушим в public git.** Отдельный канал, не trust-repo vault. |
| **Mint attestation verifier (on-chain)** | Public (проверка подписи) |
| **Attestation issuer service** | Private |
| **Deposit watcher** | Private (слушает RPC, пишет DB) |
| **Refund worker** | Private |
| **KYT client library** | Private (или public stub + private real) |
| **Circuit breaker API** | Private endpoint; public только факт «paused» on-chain/`set_paused` |
| **Convert SOL→USDT SOP** | Private runbook; public one-liner «через лицензированные рельсы entity» |
| **Dashboard live API URL** | Public client ok; backend auth/secrets private |
| **Demo/mock cabinet** | Public ok |
| **Mainnet keypairs / upgrade authority** | Private; в README только pubkey + Squads/multisig policy |

**Правило отсечения:**  
если файл помогает **торговать, исполнять, конвертировать или хранить ключи** → private.  
если файл помогает **понять продукт, проверить контракт, пройти UX депозита** → public.

---

## Как держать границу на практике

1. **Два репо:** `chillercoin` (public) и private monorepo/VPS. Не один `.gitignore` на всё.
2. Sync в public — **ручной/скриптовый export** allowlist (как сейчас `chillercoin-public`), не `git subtree` всего Crypto-Code.
3. Pre-push checklist: нет `.env`, нет `*key*`, нет `api_secret`, нет trading py, нет raw KYT.
4. README public = Bybit sleeve / no yield promise / KYT eligibility — без деталей стратегий.
5. Program upgrade: публикуем **новый** verified source под тот же program id (или объявляем новый id).

---

## Согласовано с владельцем

- Chiller Coin + сайт → public  
- Торговля и торговая система → не публикуем  
- Остальной план (KYT, attestation, quarantine, full refund, curated trust git) → ok

## Pre-push gate

```bash
cd chillercoin-public
node scripts/public-push-audit.mjs
# report → scripts/reports/LATEST.md
./scripts/install-pre-push-hook.sh   # optional: block git push on FAIL
```

- Exit 0: clean
- Exit 1: BLOCK — do not push
- Exit 2: WARN — read report; override with `PUBLIC_PUSH_ALLOW_WARN=1 git push`
