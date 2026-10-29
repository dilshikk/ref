# Реферальный бот

Telegram-бот (aiogram 3 + PostgreSQL): пользователи приглашают друзей в группу по
персональной ссылке и получают деньги за каждого вступившего, затем выводят их на
карту или телефон. Администраторы проверяют выплаты, управляют тарифами,
каналами обязательной подписки и рассылками.

## Запуск в Docker

```bash
cp .env.example .env                                  # заполнить токен, админов, БД
mkdir -p config && cp data/url.yml.example config/url.yml
sudo chown -R 1000:1000 config                        # админка сохраняет url.yml атомарно
docker compose up -d --build
docker compose logs -f bot
```

Подробности — в комментариях `docker-compose.yml` и `.env.example`.

## Запуск без Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && cp data/url.yml.example data/url.yml
python app.py
```

## Настройки

- `.env` — секреты и роли: `BOT_TOKEN`, `ADMINS`, `MONEY_ADMIN` (+ необязательный
  `MONEY_ADMINS`), `GROUP_ID`, доступ к БД, `PAYOUT_ENCRYPTION_KEY`.
- `url.yml` — тарифы и лимиты (меняются из админки без перезапуска). Читаются только
  через `data/settings.py: get_settings()` — мусорные значения заменяются значениями
  по умолчанию.

## Проверки и тесты

```bash
pip install -r requirements-dev.txt
ruff check .
python -m pytest tests -q
```

То же самое запускается на GitHub при каждом push и pull request
(`.github/workflows/ci.yml`).

## Структура

| Папка | Что внутри |
|---|---|
| `handlers/` | Обработчики: `private/` — личка и админка, `groups/` — вступления и выходы из группы |
| `middlewares/` | Блокировка, обязательная подписка, антиспам, журнал действий |
| `services/` | Подписка, рассылки, проверка добавленных участников, аудит |
| `utils/` | Чистая логика (деньги, капча, реквизиты, подписка) и работа с БД (`utils/db/`) |
| `data/` | Загрузка `.env` и `url.yml` |
| `tests/` | Тесты на pytest |
