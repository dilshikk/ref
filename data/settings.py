"""
Единая точка чтения настроек бота (#22, #113).

Настройки хранятся в таблице bot_settings (ключ → JSON). При старте бот один раз
читает её в память (services/settings_store.py), а админка при сохранении обновляет
и базу, и эту копию. Поэтому get_settings() остаётся синхронной, не ходит в базу
и изменения из админки действуют сразу.

Раньше каждый файл читал url.yml сам и со своим умолчанием: цена за реферала
в одном месте была 1, в другом 0; лимит вывода — 0 или 9 999 999. Кроме того,
число могло сохраниться строкой ("500"), и тогда сравнение баланса падало.
Теперь все читают get_settings(): типы и умолчания описаны здесь один раз.

Модуль не зависит от aiogram, базы и .env — его можно тестировать отдельно.
"""
import html
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from loguru import logger

# Умолчания — единственный источник. Деньги по умолчанию не начисляются (0):
# если цена не задана, лучше не заплатить, чем заплатить не то.
DEFAULT_INVITED_CREDIT = 0
DEFAULT_VOTE_CREDIT = 0
DEFAULT_MIN_BALANCE = 0
# Фактически «без ограничения», пока админ не задал лимит
DEFAULT_MAX_WITHDRAW = 9_999_999
DEFAULT_GROUP_MEMBER_COUNT = 3
# Антифрод (#18): было 100 в час. Вступления сверх лимита не теряются, а ждут
# решения админа (/holds), поэтому лимит можно держать низким.
DEFAULT_ANTISPAM_REFERRAL_LIMIT = 20
DEFAULT_ANTISPAM_REFERRAL_DAILY_LIMIT = 100
DEFAULT_REFERRAL_HOLD_HOURS = 72
# Добавленный в группу засчитывается в «добавил N участников» только после того,
# как пробыл в группе add_min_hours и набрал add_min_score (см. utils/account_quality.py).
# 0 и 0 — проверка выключена (как до #18).
DEFAULT_ADD_MIN_HOURS = 24
DEFAULT_ADD_MIN_SCORE = 2
DEFAULT_POLL_QUESTION = "Насколько вам удобен наш бот?"
DEFAULT_POLL_OPTIONS: tuple[str, ...] = ("Отлично", "Нормально", "Нужно улучшить")
_MAX_POLL_OPTIONS = 10  # ограничение Telegram

# Шаблоны «📝 Тексты» (#88): какие тексты админ может заменить своими
TEXT_TEMPLATE_KEYS: tuple[str, ...] = ("start", "help", "support")
# Подстановка имени пользователя в шаблон
NAME_PLACEHOLDER = "{name}"

# Все ключи, которые бот хранит в bot_settings. Неизвестный ключ админка не сохранит.
SETTING_KEYS: frozenset[str] = frozenset({
    "invited_credit",
    "vote_credit",
    "min_balance",
    "max_withdraw",
    "group_member_count",
    "antispam_referral_limit",
    "antispam_referral_daily_limit",
    "referral_hold_hours",
    "add_min_hours",
    "add_min_score",
    "subcr_channel",     # ссылка на группу
    "payment_channel",   # публичная ссылка на чат выплат
    "payment_chat_id",   # куда бот пишет отчёты о выплатах
    "url",               # ссылка «Проголосовать»
    "support",
    "poll_question",
    "poll_options",
    "text_templates",    # {"start": ..., "help": ..., "support": ...}
    "group_id",          # группа для реферальных ссылок (раньше GROUP_ID в .env, #116)
    "log_group_id",      # группа для логов (раньше LOG_GROUP_ID в .env)
    "money_admins",      # кто одобряет выплаты (раньше MONEY_ADMIN / MONEY_ADMINS в .env)
})

# Копия таблицы bot_settings в памяти. Заполняется при старте и при каждом сохранении.
_values: dict[str, Any] = {}


def replace_cache(values: Mapping[str, Any]) -> None:
    """Подменяет всю копию настроек (после чтения из базы)."""
    _values.clear()
    _values.update(values)


def cache_set(key: str, value: Any) -> None:
    """Обновляет одну настройку в памяти (после записи в базу)."""
    _values[key] = value


def get_raw(key: str, default: Any = None) -> Any:
    """Значение как оно сохранено — для текстов, аудита и словарей."""
    return _values.get(key, default)


def snapshot() -> dict[str, Any]:
    return dict(_values)


@dataclass(frozen=True)
class Settings:
    invited_credit: int          # сум за приглашённого
    vote_credit: int             # сум за голос в опросе
    min_balance: int             # минимальная сумма вывода
    max_withdraw: int            # максимальная сумма одного вывода
    group_member_count: int      # сколько участников нужно добавить перед выводом
    antispam_referral_limit: int # максимум оплачиваемых рефералов в час
    antispam_referral_daily_limit: int  # максимум оплачиваемых рефералов за 24 часа
    referral_hold_hours: int     # выход из группы в течение стольких часов снимает начисление (0 — всегда)
    add_min_hours: int           # сколько часов добавленный должен пробыть в группе до зачёта
    add_min_score: int           # минимальный балл активности добавленного
    group_link: str              # ссылка на группу (subcr_channel)
    payment_channel: str         # публичная ссылка на чат выплат
    vote_url: str                # ссылка «Проголосовать» (url)
    support: str                 # контакт поддержки
    poll_question: str
    poll_options: tuple[str, ...]
    group_id: int | None         # группа реферальных ссылок; None — ещё не задана в админке
    log_group_id: int | None     # группа логов; None — логи в группу не шлём
    money_admins: tuple[int, ...]  # кто может одобрять выплаты и менять балансы
    text_templates: dict[str, str]  # непустые шаблоны из «📝 Тексты» (#88)


def _to_int(raw: Any) -> int | None:
    """Целое из сохранённого значения: строка " 500 " принимается, bool и мусор — нет."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _int(key: str, default: int, minimum: int = 0) -> int:
    """Целое число. Мусор и слишком малые значения — умолчание."""
    raw = _values.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    value = _to_int(raw)
    if value is None:
        logger.warning(f"Настройка {key}={raw!r} не число, используется {default}")
        return default
    if value < minimum:
        logger.warning(f"Настройка {key}={value} меньше {minimum}, используется {default}")
        return default
    return value


def _chat_id(key: str) -> int | None:
    """ID чата (отрицательное число). Пусто или мусор — None."""
    raw = _values.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    value = _to_int(raw)
    if value is None or value == 0:
        logger.warning(f"Настройка {key}={raw!r} не ID чата — не задана")
        return None
    return value


def _user_ids(key: str) -> tuple[int, ...]:
    raw = _values.get(key)
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    elif isinstance(raw, (int, str)) and not isinstance(raw, bool):
        items = str(raw).replace(",", " ").split()
    else:
        items = []
    ids = {v for v in (_to_int(x) for x in items) if v is not None and v > 0}
    return tuple(sorted(ids))


def _str(key: str) -> str:
    raw = _values.get(key)
    return "" if raw is None else str(raw).strip()


def _poll_options() -> tuple[str, ...]:
    raw = _values.get("poll_options")
    if isinstance(raw, list):
        options = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        options = [part.strip() for part in raw.split("|") if part.strip()]
    else:
        options = []
    if len(options) < 2:
        return DEFAULT_POLL_OPTIONS
    return tuple(options[:_MAX_POLL_OPTIONS])


def _text_templates() -> dict[str, str]:
    """Только известные ключи и непустые строки: пустой шаблон = текст по умолчанию."""
    raw = _values.get("text_templates")
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for key in TEXT_TEMPLATE_KEYS:
        value = raw.get(key)
        if value is None or isinstance(value, (dict, list, bool)):
            continue
        text = str(value).strip()
        if text:
            out[key] = text
    return out


def get_settings() -> Settings:
    return Settings(
        invited_credit=_int("invited_credit", DEFAULT_INVITED_CREDIT),
        vote_credit=_int("vote_credit", DEFAULT_VOTE_CREDIT),
        min_balance=_int("min_balance", DEFAULT_MIN_BALANCE),
        max_withdraw=_int("max_withdraw", DEFAULT_MAX_WITHDRAW, minimum=1),
        group_member_count=_int("group_member_count", DEFAULT_GROUP_MEMBER_COUNT),
        antispam_referral_limit=_int("antispam_referral_limit", DEFAULT_ANTISPAM_REFERRAL_LIMIT, minimum=1),
        antispam_referral_daily_limit=_int(
            "antispam_referral_daily_limit", DEFAULT_ANTISPAM_REFERRAL_DAILY_LIMIT, minimum=1
        ),
        referral_hold_hours=_int("referral_hold_hours", DEFAULT_REFERRAL_HOLD_HOURS),
        add_min_hours=_int("add_min_hours", DEFAULT_ADD_MIN_HOURS),
        add_min_score=_int("add_min_score", DEFAULT_ADD_MIN_SCORE),
        group_link=_str("subcr_channel"),
        payment_channel=_str("payment_channel"),
        vote_url=_str("url"),
        support=_str("support"),
        poll_question=_str("poll_question") or DEFAULT_POLL_QUESTION,
        poll_options=_poll_options(),
        group_id=_chat_id("group_id"),
        log_group_id=_chat_id("log_group_id"),
        money_admins=_user_ids("money_admins"),
        text_templates=_text_templates(),
    )


def custom_text(key: str, name: str = "") -> str | None:
    """
    Текст из «📝 Тексты» (#88), готовый к отправке с parse_mode="HTML", или None,
    если админ его не задавал (тогда используется текст по умолчанию).
    Шаблон экранируется целиком: символы < > & из админки не ломают сообщение.
    {name} заменяется на имя пользователя (тоже экранированное).
    """
    raw = get_settings().text_templates.get(key)
    if not raw:
        return None
    return html.escape(raw).replace(NAME_PLACEHOLDER, html.escape(name))


def is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


# ── Разбор ввода админа (#116) ──────────────────────────────────────────
def parse_group_chat_id(text: str) -> int | None:
    """'-1001234567890' → int. Другие форматы — None."""
    raw = text.strip()
    if raw.startswith("-100") and raw[1:].isdigit() and len(raw) > 4:
        return int(raw)
    return None


def parse_user_ids(text: str) -> tuple[int, ...] | None:
    """'111, 222 333' → (111, 222, 333). Пусто или не числа — None."""
    parts = text.replace(",", " ").split()
    if not parts or not all(p.isdigit() and int(p) > 0 for p in parts):
        return None
    return tuple(sorted({int(p) for p in parts}))
