# План исправлений #18: слабый антифрод

Состояние кода: `main` @ `096b04f` (после #14, #19–#23).

Задача делится на 4 независимые части. Каждую можно делать отдельным PR в
указанном порядке: от простой и безопасной к сложной.

| # | Часть | Файлы | Сложность | Риск |
|---|-------|-------|-----------|------|
| 1 | Снизить лимит вступлений в час | `data/settings.py`, `data/url.yml.example` | низкая | низкий |
| 2 | Награда за голос только с подпиской | `handlers/private/fallback.py`, `services/subscription.py` | низкая | низкий |
| 3 | Капча сложнее + лимит попыток | `handlers/private/withdrawals.py`, новый `utils/captcha.py` | средняя | низкий |
| 4 | «Добавленные» засчитываются только после проверки | `handlers/groups/member_events.py`, `withdrawals.py`, БД, новый `utils/account_quality.py` | высокая | средний |

---

## Часть 1. Лимит вступлений в час

**Сейчас:** `DEFAULT_ANTISPAM_REFERRAL_LIMIT = 100` (`data/settings.py`).
`credit_referral` в `member_events.py` при превышении лимита уже не теряет
начисление, а кладёт его «на удержание» (`db.hold_referral`, решение через
`/holds`). Значит, лимит можно снизить без потери денег честных пользователей.

**Сделать:**
1. `data/settings.py`: `DEFAULT_ANTISPAM_REFERRAL_LIMIT = 20`.
2. Добавить суточный лимит:
   - `DEFAULT_ANTISPAM_REFERRAL_DAILY_LIMIT = 100`, поле
     `antispam_referral_daily_limit: int` в `Settings`, ключ
     `antispam_referral_daily_limit` в `get_settings()` (`minimum=1`).
   - В `ReferralDatabase` добавить `get_referrals_last_day(referrer_id)` по
     аналогии с `get_referrals_last_hour` (тот же индекс по `user_id` и
     `invited_at`).
   - В `credit_referral` проверять оба лимита. Сработал любой — то же
     удержание через `hold_referral` и `_antifraud_alert`. В тексте
     уведомления указывать, какой лимит сработал («за час» или «за сутки»).
3. `data/url.yml.example`: добавить оба ключа с комментариями.
4. Важно: если в рабочем `url.yml` уже записан `antispam_referral_limit: 100`
   (через «🚨 Антифрод лимит» в админке), новое умолчание не применится.
   После выкладки поменять значение в админке вручную. Указать это в описании PR.

**Тесты** (`tests/test_settings.py`): новое умолчание, разбор
`antispam_referral_daily_limit`, отклонение `0` и мусора.

**Готово, когда:** 21-е вступление за час и 101-е за сутки уходят на
удержание, пригласивший и админы получают уведомление.

---

## Часть 2. Награда за голос только с подпиской

**Сейчас:** `SubscriptionMiddleware` подключён только к `dispatcher.message`
и `dispatcher.callback_query` (`app.py`, `setup_middlewares`). Событие
`poll_answer` его не проходит. К тому же у `PollAnswer` поле `user`, а не
`from_user`, поэтому middleware его всё равно пропустил бы. В итоге
`poll_answer_handler` (`handlers/private/fallback.py`) начисляет
`vote_credit` любому проголосовавшему.

**Решение:** проверять подписку прямо в обработчике, а не через middleware.
Если middleware отбросит событие, голос не запишется и пользователь не поймёт
почему. Проверка в обработчике записывает голос, но без подписки награду не
даёт и объясняет причину.

**Сделать:**
1. `services/subscription.py`: добавить строгий вариант проверки для денежных
   операций:
   ```python
   async def is_user_subscribed_strict(user_id: int) -> bool:
       """Для начислений: любая ошибка проверки = НЕ подписан."""
   ```
   Сейчас `_check_channels` при `TelegramBadRequest` и других ошибках делает
   `continue`, то есть засчитывает подписку. Для денег так нельзя. Вынести цикл
   в `_check_channels(user_id, *, fail_closed: bool)`, проверять каналы
   параллельно через `asyncio.gather(..., return_exceptions=True)` (заодно
   пункт из #24). При `fail_closed=True` любое исключение означает `False`.
   Строгую проверку не кэшировать: `use_cache=False`.
2. `fallback.py`, `poll_answer_handler`: после `upsert_poll_vote_answer` и до
   `reward_vote_once`:
   ```python
   if reward > 0 and user.id not in ADMINS and not await is_user_subscribed_strict(user.id):
       # голос записан, награды нет
       await bot.send_message(user.id, "Спасибо за голос! Чтобы получить награду, "
                              "подпишитесь на все каналы и проголосуйте снова 👇",
                              reply_markup=await subscribe_keyboard())
       return
   ```
   `reward_vote_once` начисляет один раз на пару (пользователь, вопрос).
   Человек, который подписался и проголосовал повторно (отозвал голос и выбрал
   вариант заново), получит награду ровно один раз.
3. Обёртку `try/except` вокруг проверки оставить: при сбое Telegram API
   награду не давать и записать предупреждение в лог.

**Тесты** (`tests/test_poll_reward.py`, мокаем `db`, `bot`,
`is_user_subscribed_strict`):
- подписан: `reward_vote_once` вызван;
- не подписан: не вызван, `send_message` с клавиатурой подписки;
- проверка упала с исключением: не вызван;
- `vote_credit = 0`: подписка не проверяется.

**Готово, когда:** голос без подписки записывается, но денег не приносит.

---

## Часть 3. Капча и лимит попыток

**Сейчас** (`withdrawals.py`, `ask_captcha` и `check_captcha`): `a + b` из
цифр 1–9, всего 17 возможных ответов (2–18). Количество попыток не
ограничено, ответ после ошибки не меняется. Скрипт перебирает ответ за
секунды.

**Решение:** капча кнопками со случайными вариантами, новая капча после каждой
ошибки, 3 попытки, после них блокировка вывода на 15 минут.

**Сделать:**
1. Новый модуль `utils/captcha.py` (чистые функции, без aiogram, легко
   тестировать):
   ```python
   @dataclass(frozen=True)
   class Captcha:
       question: str          # "37 − 9 = ?"
       answer: int
       options: list[int]     # 4 варианта, правильный на случайной позиции
       token: str             # secrets.token_hex(4), привязка кнопок к этой капче

   def generate() -> Captcha: ...
   def is_locked(locked_until: float | None, now: float) -> bool: ...
   ```
   - Операции `+`, `−`, `×`, числа 10–49 (для `×` одно из чисел 2–9).
     Вычитание без отрицательных ответов.
   - Неправильные варианты: ответ ±1…±10, без повторов и отрицательных чисел.
   - Константы: `MAX_ATTEMPTS = 3`, `LOCK_SECONDS = 15 * 60`, `TTL_SECONDS = 5 * 60`.
2. `ask_captcha`: `generate()`, сохранить в FSM `captcha_answer`,
   `captcha_token`, `captcha_issued_at = time.time()`, при первом показе
   `captcha_attempts = 0`. Отправить inline-кнопки с
   `callback_data=f"cap:{token}:{option}"`.
3. Новый обработчик
   `@router.callback_query(F.data.startswith("cap:"), WithdrawalFlow.CAPTCHA)`:
   - токен не совпадает (старое сообщение): ответить «Капча устарела» и не
     засчитывать попытку;
   - прошло больше `TTL_SECONDS`: выдать новую капчу;
   - неверный ответ: `attempts += 1`. Если `attempts >= MAX_ATTEMPTS`,
     сохранить `captcha_locked_until = now + LOCK_SECONDS`, вызвать
     `state.set_state(None)` (не `state.clear()`, чтобы данные о блокировке
     сохранились), записать `audit_log_service.record_user_action(...,
     action="withdraw_captcha_locked")` и сообщить пользователю. Иначе выдать
     новую капчу с текстом «Неверно, осталось N попыток»;
   - верный ответ: то, что сейчас делает `check_captcha` после проверки
     (сообщение про N участников и переход в `WithdrawalFlow.CARD`). Удалить из
     FSM поля `captcha_*`.
4. Старый `check_captcha` (текстовый ответ) заменить обработчиком
   `@router.message(WithdrawalFlow.CAPTCHA)`, который отвечает «Выберите ответ
   кнопкой ниже» и не засчитывает попытку.
5. `_do_withdraw`: в начале проверить `captcha_locked_until` из
   `state.get_data()`. Если блокировка активна, ответить «Вывод временно
   недоступен, попробуйте через N мин.» и выйти.
6. Блокировку хранить в FSM (`PostgresStorage`), она переживает рестарт. Чтобы
   её нельзя было сбросить: проверить, что `/start` и «❌ Отмена»
   (`cancel_withdraw`) не вызывают `state.clear()` до истечения блокировки.
   Если вызывают, перед `clear()` сохранять `captcha_locked_until` и
   записывать его обратно.

**Тесты** (`tests/test_captcha.py`):
- `generate()` 1000 раз: правильный ответ всегда среди 4 вариантов, варианты
  уникальны и неотрицательны, позиция правильного ответа случайна;
- `is_locked` до и после окончания блокировки;
- на уровне обработчика (с моками): 3 неверных ответа дают блокировку, токен
  от старой капчи не тратит попытку.

**Готово, когда:** после 3 ошибок вывод заблокирован на 15 минут, текстом
капчу не пройти, на старые кнопки бот отвечает «Капча устарела».

---

## Часть 4. Засчитывать «добавленных» только после проверки

**Сейчас:** счётчик `GroupMembers.add_user_count` растёт сразу при
вступлении:
- по реферальной ссылке: `db.add_or_update_group_member(group_id, referrer_id)`
  в `on_user_join`, если `credit_referral` вернул `True`;
- при ручном добавлении: `db.credit_manual_add_once(group_id, inviter.id, user.id)`.

Счётчик читают `check_group_members`, `confirm_withdraw` (#15) и SQL
одобрения в `payout_guard`. Фейковые аккаунты вступают, норма выполнена.

Bot API не сообщает возраст аккаунта. Доступные сигналы: аккаунт пробыл в
группе X часов, написал в группу, запускал бота, есть фото профиля или
username, есть Premium.

**Решение:** при вступлении создавать запись «ожидает проверки».
Увеличивать `add_user_count` только при квалификации. Тогда код, который
читает счётчик (`withdrawals`, `payout_guard`), менять не нужно. Квалификация
запускается «лениво», по кнопке «✅ Я добавил(а)», без фоновых задач.

**Сделать:**

1. **БД** (`repositories/`, методы `ReferralDatabase`, вызвать в
   `database_connected` в `app.py`):
   ```sql
   CREATE TABLE IF NOT EXISTS PendingGroupAdds (
       id BIGSERIAL PRIMARY KEY,
       group_id BIGINT NOT NULL,
       inviter_id BIGINT NOT NULL,
       member_id BIGINT NOT NULL,
       source TEXT NOT NULL,              -- 'referral' | 'manual'
       joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       qualified_at TIMESTAMPTZ,
       rejected_at TIMESTAMPTZ,
       UNIQUE (group_id, member_id)       -- один человек засчитывается один раз
   );
   CREATE INDEX IF NOT EXISTS pending_adds_inviter
       ON PendingGroupAdds (group_id, inviter_id) WHERE qualified_at IS NULL AND rejected_at IS NULL;

   CREATE TABLE IF NOT EXISTS GroupMemberActivity (
       group_id BIGINT NOT NULL,
       user_id BIGINT NOT NULL,
       first_message_at TIMESTAMPTZ NOT NULL DEFAULT now(),
       message_count INT NOT NULL DEFAULT 1,
       PRIMARY KEY (group_id, user_id)
   );
   ```
   Методы:
   - `add_pending_group_add(group_id, inviter_id, member_id, source) -> bool`
     (`ON CONFLICT DO NOTHING`);
   - `list_pending_adds(group_id, inviter_id, limit=50)`;
   - `qualify_pending_add(id) -> bool`: в одной транзакции
     `UPDATE ... SET qualified_at = now() WHERE id=$1 AND qualified_at IS NULL AND rejected_at IS NULL`,
     затем, если строка изменилась, `add_or_update_group_member(group_id, inviter_id)`;
   - `reject_pending_add(id)`;
   - `touch_group_activity(group_id, user_id)` (upsert, `message_count + 1`).

2. **Запись активности в группе.** Новый middleware
   `middlewares/group_activity.py`, подключить через
   `dispatcher.message.outer_middleware(...)`. Только для `group`/`supergroup`
   и `chat.id == GROUP_ID`: вызвать `db.touch_group_activity` и передать
   событие дальше. Обработчик здесь не подходит: в aiogram 3 он забирает
   событие и останавливает остальные роутеры (это описано в комментарии в
   конце `member_events.py`). Ошибки БД ловить и логировать, событие не
   блокировать. Чтобы не нагружать БД, писать не чаще раза в 10 минут на
   пользователя (кэш в памяти, как `_last_prompt_at` в `subscription.py`).

3. **Оценка аккаунта:** новый модуль `utils/account_quality.py`, чистая функция:
   ```python
   @dataclass(frozen=True)
   class MemberSignals:
       hours_in_group: float
       still_member: bool
       wrote_in_group: bool
       started_bot: bool
       has_photo: bool
       has_username: bool
       is_premium: bool

   def evaluate(s: MemberSignals, min_hours: int, min_score: int) -> Literal["qualified", "wait", "rejected"]:
       if not s.still_member: return "rejected"
       if s.hours_in_group < min_hours: return "wait"
       score = 2*s.wrote_in_group + 2*s.started_bot + s.has_photo + s.has_username + 2*s.is_premium
       return "qualified" if score >= min_score else "wait"
   ```
   Настройки в `data/settings.py`: `add_min_hours` (умолчание 24),
   `add_min_score` (умолчание 2). Добавить в `url.yml.example`.
   Значение `0` для обоих отключает проверку для обратной совместимости.

4. **`on_user_join` (`member_events.py`):**
   - по ссылке: вместо `db.add_or_update_group_member(...)` вызывать
     `db.add_pending_group_add(group_id, referrer_id, user.id, "referral")`.
     Денежное начисление (`credit_referral`) не трогать;
   - вручную: вместо `credit_manual_add_once` вызывать
     `add_pending_group_add(..., "manual")`. Запись в старую таблицу
     `GroupManualAdds` можно оставить для истории.
   - Если `add_min_hours == 0 and add_min_score == 0`, оставить старое
     поведение (сразу увеличивать счётчик).

5. **Квалификация по кнопке** (`check_group_members` в `withdrawals.py`):
   перед чтением `get_user_add_count` вызвать новую функцию
   `services/group_adds.py::qualify_pending(group_id, inviter_id) -> QualifyReport`:
   - `list_pending_adds(..., limit=50)`;
   - для каждой записи собрать `MemberSignals`:
     `bot.get_chat_member(GROUP_ID, member_id)` (состоит ли в группе,
     `user.is_premium`, `user.username`), `bot.get_user_profile_photos(member_id, limit=1)`,
     `db.select_user(telegram_id=...)` (запускал бота; отличать от записи,
     которую `on_user_join` создаёт сам: использовать `language` или
     `created_at` по нажатию `/start`, сверить с `onboarding.py`),
     строку из `GroupMemberActivity`;
   - запросы к Telegram выполнять параллельно через
     `asyncio.gather(...)`, не больше 10 одновременно (`asyncio.Semaphore`);
   - `qualified` → `qualify_pending_add`, `rejected` → `reject_pending_add`,
     `wait` → ничего;
   - любая ошибка Telegram для записи даёт `wait`, а не `qualified`.
   - В ответе пользователю показать: «Засчитано X из N, на проверке Y (станут
     засчитаны, когда пробудут в группе 24 ч. и проявят активность)».

6. **Выход из группы:** `reverse_manual_add_on_leave` и
   `reverse_referral_on_leave` сейчас уменьшают счётчик. Добавить: если запись
   в `PendingGroupAdds` ещё не квалифицирована, пометить её `rejected_at` и
   **не** уменьшать `add_user_count`, потому что он не увеличивался. Уменьшать
   только для `qualified_at IS NOT NULL`. Это главный риск части 4: проверить
   SQL в `db.reverse_manual_add_on_leave` и `db.reverse_referral_on_leave`,
   чтобы счётчик не ушёл в минус.

7. **Админка:** в карточке пользователя (`user_management.py`) показать
   «Добавлено: засчитано X / на проверке Y / отклонено Z».

**Тесты:**
- `tests/test_account_quality.py`: все ветки `evaluate`, граница
  `min_hours`, отключение через `0/0`;
- `tests/test_group_adds.py` (моки `db` и `bot`): ошибка Telegram даёт
  `wait`, вышедший из группы даёт `rejected`, повторный вызов не квалифицирует
  дважды;
- выход до квалификации не уменьшает счётчик.

**Готово, когда:** 3 свежих пустых аккаунта, которые вступили и ничего не
делали, не выполняют норму. 3 реальных участника, которые пробыли сутки и
написали в группу, выполняют её по кнопке «✅ Я добавил(а)».

---

## Выкладка

1. Части 1 и 2: сразу, без миграций.
2. Часть 3: миграций нет, данные в FSM. Незаконченные формы на шаге капчи
   после выкладки получат «Капча устарела», и пользователь начнёт заново.
3. Часть 4: миграции идемпотентные (`IF NOT EXISTS`). Уже накопленные
   `add_user_count` не пересчитывать. Новые правила действуют только для
   вступлений после выкладки. Сначала выложить с `add_min_hours=0`,
   `add_min_score=0` (старое поведение) и проверить, что записи в
   `PendingGroupAdds` создаются. Затем включить `24/2` в `url.yml`.
4. После каждой части: `ruff check .`, `pytest`.

## Чек-лист для #18

- [ ] Лимит в час снижен до 20, добавлен суточный лимит
- [ ] Награда за голос только с подпиской (строгая проверка)
- [ ] Капча кнопками, 3 попытки, блокировка 15 мин
- [ ] «Добавленные» засчитываются только после квалификации
- [ ] Тесты для каждой части
