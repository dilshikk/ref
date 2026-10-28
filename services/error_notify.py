"""
Уведомление админов об ошибках.

Почему раньше сообщения не доходили:
- traceback вставлялся в HTML без экранирования (в нём есть «<module>», «<lambda>») —
  Telegram отвечал «can't parse entities» и ничего не отправлялось;
- длинный traceback превышал лимит 4096 символов;
- ошибки в обработчиках вообще не отправлялись, только писались в лог.
"""
import html
import time
import traceback

from aiogram import Bot
from loguru import logger

from data.config import ADMINS

_MAX_TRACE_CHARS = 3000  # запас до лимита Telegram в 4096 символов с учётом заголовка
_DEDUP_WINDOW_SEC = 300   # одинаковая ошибка — не чаще раза в 5 минут, чтобы не заспамить админа
_last_sent: dict[str, float] = {}


def _signature(error: BaseException) -> str:
    tb = traceback.extract_tb(error.__traceback__)
    where = f"{tb[-1].filename}:{tb[-1].lineno}" if tb else "?"
    return f"{type(error).__name__}@{where}"


def _should_send(signature: str) -> tuple[bool, int]:
    """Возвращает (отправлять ли, сколько секунд прошло с прошлой отправки)."""
    now = time.time()
    for key in [k for k, t in _last_sent.items() if now - t > _DEDUP_WINDOW_SEC]:
        del _last_sent[key]
    if signature in _last_sent:
        return False, int(now - _last_sent[signature])
    _last_sent[signature] = now
    return True, 0


def _format(title: str, error: BaseException, context: str | None) -> str:
    trace = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    if len(trace) > _MAX_TRACE_CHARS:
        # Важнее всего конец traceback — там место и текст ошибки
        trace = "…\n" + trace[-_MAX_TRACE_CHARS:]
    parts = [f"⚠️ <b>{html.escape(title)}</b>"]
    if context:
        parts.append(html.escape(context[:400]))
    parts.append(f"<pre>{html.escape(trace)}</pre>")
    return "\n\n".join(parts)


async def notify_admins_about_error(
    bot: Bot,
    error: BaseException,
    title: str = "Ошибка в боте",
    context: str | None = None,
) -> None:
    send, _ = _should_send(_signature(error))
    if not send:
        logger.debug(f"Ошибка {_signature(error)} уже отправлена недавно — пропускаю")
        return

    text = _format(title, error, context)
    plain = f"⚠️ {title}\n\n{type(error).__name__}: {str(error)[:1500]}"
    for admin_id in ADMINS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception as e:
            # Запасной вариант: короткий текст без разметки
            logger.warning(f"Не удалось отправить ошибку админу {admin_id} с форматированием: {e}")
            try:
                await bot.send_message(admin_id, plain)
            except Exception as e2:
                logger.error(f"Не удалось отправить ошибку админу {admin_id}: {e2}")
