# services/broadcast_runner.py
"""
Выполнение рассылок (#19).

Все рассылки (и «Отправить сейчас», и запланированные) — задачи в
scheduled_broadcasts. Их выполняет один фоновый цикл:

- получатели идут по возрастанию Users.id, после каждого получателя прогресс
  (last_user_pk и счётчики) сохраняется в базе. При перезапуске задача
  возвращается в очередь и продолжается с места остановки — повторно
  сообщение может получить максимум один человек (на котором оборвалась отправка);
- круг получателей фиксируется при первом запуске (max_user_pk), поэтому
  после рестарта он не «расползается»;
- TelegramRetryAfter — ожидание и повтор, заблокировавшие бота помечаются
  и в следующие рассылки не попадают;
- рассылку можно отменить во время отправки: статус проверяется на каждой пачке.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, cast

from loguru import logger

from loader import bot as app_bot, db
from services.audit_log import audit_log_service
from utils.broadcast_send import InvalidBroadcast, SendResult, send_one, validate_payload

POLL_INTERVAL_SEC = 5
BATCH_SIZE = 50
# Telegram: ~30 сообщений в секунду на бота. 0.05 с оставляет запас.
SEND_DELAY_SEC = 0.05
PROGRESS_EDIT_EVERY = 100

_task: asyncio.Task[Any] | None = None
_wake = asyncio.Event()


def _parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, dict) else {}
        except ValueError:
            return {}
    return {}


def wake_up() -> None:
    """Новая задача «Отправить сейчас»: не ждём следующего опроса."""
    _wake.set()


async def _edit_progress(payload: dict[str, Any], text: str) -> None:
    chat_id = int(payload.get("progress_chat_id") or 0)
    message_id = int(payload.get("progress_message_id") or 0)
    if not chat_id or not message_id:
        return
    try:
        await app_bot.edit_message_text(text=text, chat_id=chat_id, message_id=message_id, parse_mode="HTML")
    except Exception as e:
        # «message is not modified» и удалённое сообщение не должны мешать рассылке
        logger.debug(f"Не удалось обновить прогресс рассылки: {e}")


def _summary(title: str, job_id: int, total: int, success: int, failed: int, blocked: int) -> str:
    return (
        f"{title}\n\n"
        f"🆔 Задача: <code>{job_id}</code>\n"
        f"👥 Получателей: <b>{total}</b>\n"
        f"✅ Доставлено: <b>{success}</b>\n"
        f"🚫 Заблокировали бота: <b>{blocked}</b>\n"
        f"⚠️ Ошибок: <b>{failed}</b>"
    )


def _kw(counts: dict[str, int]) -> dict[str, int]:
    return {"total": counts["total"], "success": counts["success"], "failed": counts["failed"], "blocked": counts["blocked"]}


async def _is_still_running(job_id: int) -> bool:
    row = cast(
        dict[str, Any] | None,
        await db.execute("SELECT status FROM scheduled_broadcasts WHERE id = $1", parameters=(job_id,), fetchone=True),
    )
    return bool(row) and row.get("status") == "running"


async def _log(job: dict[str, Any], status: str, counts: dict[str, int], error_text: str | None = None) -> None:
    payload = _parse_payload(job.get("payload"))
    await db.insert_broadcast_send_log(
        source=str(job.get("source") or "scheduled"),
        actor_id=int(job.get("actor_id") or 0) or None,
        scheduled_job_id=int(job["id"]),
        entity_id=str(job["id"]),
        status=status,
        total_users=counts["total"],
        success_count=counts["success"],
        failed_count=counts["failed"] + counts["blocked"],
        error_text=error_text,
        payload={"media_type": payload.get("media_type"), "blocked": counts["blocked"]},
    )


async def _run_job(job: dict[str, Any]) -> None:
    job_id = int(job["id"])
    actor_id = int(job.get("actor_id") or 0)
    payload = _parse_payload(job.get("payload"))
    resumed = int(job.get("last_user_pk") or 0) > 0
    counts = {
        "total": int(job.get("total_users") or 0),
        "success": int(job.get("success_count") or 0),
        "failed": int(job.get("failed_count") or 0),
        "blocked": int(job.get("blocked_count") or 0),
    }
    last_pk = int(job.get("last_user_pk") or 0)

    try:
        validate_payload(payload)
    except InvalidBroadcast as e:
        await db.mark_scheduled_broadcast_failed(job_id=job_id, error_text=str(e))
        await _log(job, "failed", counts, str(e))
        await _edit_progress(payload, f"❌ Рассылка #{job_id} не отправлена: {e}")
        return

    max_pk = await db.fix_broadcast_audience(job_id)
    if counts["total"] <= 0:
        counts["total"] = await db.count_broadcast_recipients(max_pk)
        await db.set_broadcast_total(job_id, counts["total"])

    await audit_log_service.record_admin_action(
        actor_id=actor_id,
        action="broadcast_resumed" if resumed else "broadcast_started",
        entity_type="broadcast",
        entity_id=str(job_id),
        payload={"total_users": counts["total"], "from_user_pk": last_pk, "media_type": payload.get("media_type")},
    )

    processed_since_edit = 0
    while True:
        if not await _is_still_running(job_id):
            logger.info(f"Рассылка {job_id} остановлена (отменена админом)")
            await _edit_progress(payload, _summary(f"🛑 Рассылка #{job_id} отменена", job_id, **_kw(counts)))
            return

        batch = cast(list[dict[str, Any]], await db.get_broadcast_recipients_after(last_pk, max_pk, BATCH_SIZE) or [])
        if not batch:
            break

        for user in batch:
            chat_id = int(user.get("telegram_id") or 0)
            if chat_id > 0:
                result, error = await send_one(app_bot, chat_id, payload)
                if result is SendResult.OK:
                    counts["success"] += 1
                elif result is SendResult.BLOCKED:
                    counts["blocked"] += 1
                    await db.mark_bot_blocked(chat_id)
                else:
                    counts["failed"] += 1
                    logger.warning(f"Рассылка {job_id}: ошибка для {chat_id}: {error}")
                await asyncio.sleep(SEND_DELAY_SEC)
            else:
                counts["failed"] += 1
            last_pk = int(user["id"])
            processed_since_edit += 1
            # Прогресс — после КАЖДОГО получателя: после рестарта повторно
            # получит сообщение максимум тот, кому отправка шла в момент падения
            await db.save_broadcast_progress(job_id, last_pk, counts["success"], counts["failed"], counts["blocked"])

        if processed_since_edit >= PROGRESS_EDIT_EVERY:
            processed_since_edit = 0
            done = counts["success"] + counts["failed"] + counts["blocked"]
            await _edit_progress(
                payload,
                _summary(f"📦 Рассылка идёт... {done}/{counts['total']}", job_id, **_kw(counts)),
            )

    if not await _is_still_running(job_id):
        # Отменили во время последней пачки: итог уже записан обработчиком отмены
        await _edit_progress(payload, _summary(f"🛑 Рассылка #{job_id} отменена", job_id, **_kw(counts)))
        return

    await db.finish_broadcast(job_id, counts["total"], counts["success"], counts["failed"], counts["blocked"])
    await _log(job, "done", counts)
    await audit_log_service.record_admin_action(
        actor_id=actor_id,
        action="broadcast_job_finished",
        entity_type="broadcast",
        entity_id=str(job_id),
        payload={
            "total_users": counts["total"],
            "success": counts["success"],
            "failed": counts["failed"],
            "blocked": counts["blocked"],
        },
    )
    final_text = _summary(f"✅ <b>Рассылка #{job_id} завершена</b>", job_id, **_kw(counts))
    if payload.get("progress_message_id"):
        await _edit_progress(payload, final_text)
    elif actor_id:
        try:
            await app_bot.send_message(actor_id, final_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Не удалось отправить итог рассылки {job_id} админу: {e}")


async def _process(job: dict[str, Any]) -> None:
    job_id = int(job.get("id") or 0)
    try:
        await _run_job(job)
    except asyncio.CancelledError:
        # Остановка бота: статус остаётся running, при старте задача вернётся
        # в очередь и продолжится с сохранённого места
        raise
    except Exception as e:
        logger.exception(f"Ошибка рассылки {job_id}: {e}")
        await db.mark_scheduled_broadcast_failed(job_id=job_id, error_text=str(e))
        counts = {"total": int(job.get("total_users") or 0), "success": 0, "failed": 0, "blocked": 0}
        await _log(job, "failed", counts, str(e))


async def _loop() -> None:
    while True:
        try:
            jobs = cast(list[dict[str, Any]], await db.claim_due_scheduled_broadcasts(limit=5) or [])
            for job in jobs:
                await _process(job)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"Ошибка цикла рассылок: {e}")

        _wake.clear()
        try:
            await asyncio.wait_for(_wake.wait(), timeout=POLL_INTERVAL_SEC)
        except asyncio.TimeoutError:
            pass


async def start_broadcast_scheduler() -> None:
    global _task
    if _task is not None and not _task.done():
        return
    # Прерванные рестартом задачи возвращаются в очередь; прогресс сохраняется
    await db.requeue_running_scheduled_broadcasts()
    _task = asyncio.create_task(_loop(), name="broadcast-runner")
    logger.info("✅ Планировщик рассылок запущен")


async def stop_broadcast_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    finally:
        _task = None
    logger.info("🛑 Планировщик рассылок остановлен")
