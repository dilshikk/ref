# data/settings.py
"""
Единая точка чтения настроек из url.yml.

Раньше каждый файл читал url_yaml сам и со своим умолчанием: цена за реферала
в одном месте была 1, в другом 0; лимит вывода — 0 или 9 999 999. Кроме того,
админка может сохранить число строкой ("500"), и тогда сравнение баланса падало,
а «10 × цена» превращалось в строку "500500500...".

Теперь все читают get_settings(): типы и умолчания описаны здесь один раз.
Настройки читаются при каждом вызове, поэтому изменения из админки действуют сразу.
"""
from dataclasses import dataclass

from loguru import logger

from data.config import url_yaml

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


def _int(key: str, default: int, minimum: int = 0) -> int:
    """Целое из url.yml. Строка "500" принимается; мусор и слишком малые значения — умолчание."""
    raw = url_yaml.get(key)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    if isinstance(raw, bool):
        logger.warning(f"url.yml: {key}={raw!r} не число, используется {default}")
        return default
    try:
        value = int(raw) if isinstance(raw, (int, float)) else int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(f"url.yml: {key}={raw!r} не число, используется {default}")
        return default
    if value < minimum:
        logger.warning(f"url.yml: {key}={value} меньше {minimum}, используется {default}")
        return default
    return value


def _str(key: str) -> str:
    raw = url_yaml.get(key)
    return "" if raw is None else str(raw).strip()


def _poll_options() -> tuple[str, ...]:
    raw = url_yaml.get("poll_options")
    if isinstance(raw, list):
        options = [str(x).strip() for x in raw if str(x).strip()]
    elif isinstance(raw, str):
        options = [part.strip() for part in raw.split("|") if part.strip()]
    else:
        options = []
    if len(options) < 2:
        return DEFAULT_POLL_OPTIONS
    return tuple(options[:_MAX_POLL_OPTIONS])


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
    )


def is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")
