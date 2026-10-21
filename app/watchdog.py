"""Сторож. Запущен в каждом процессе API и делает две вещи:

1. Снимает попытки с истёкшим дедлайном: провайдер молчит дольше PROVIDER_TIMEOUT
   или процесс упал посреди POST /tasks (истекла аренда dispatching). Это отказ
   technical, и дальше задача идёт через тот же fail_locked, что и вебхук failed.
2. Отправляет pending-попытки, которые никто не отправил: процесс упал между
   коммитом и фоновой отправкой.

Работа не задваивается: просроченную попытку берут вместе с блокировкой её задачи
через FOR UPDATE SKIP LOCKED, и другой процесс эту задачу просто пропускает.
Брошенную pending-попытку могут найти несколько процессов, но отправит её один:
dispatch() захватывает попытку атомарным UPDATE … WHERE status = 'pending'.
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app import dispatcher, orchestrator
from app.config import get_settings
from app.db import SessionFactory
from app.models import WAITING_ATTEMPT_STATUSES, Task, TaskAttempt

log = logging.getLogger(__name__)

# Сколько просроченных попыток один процесс разбирает за проход.
BATCH = 100


async def run_forever() -> None:
    interval = get_settings().watchdog_interval_seconds
    while True:
        try:
            await sweep()
        except Exception:
            log.exception("watchdog sweep failed")
        await asyncio.sleep(interval)


async def sweep() -> None:
    for _ in range(BATCH):
        if not await expire_one():
            break
    for attempt_id in await orphaned_pending():
        dispatcher.schedule(attempt_id)


async def expire_one() -> bool:
    """Снимает одну просроченную попытку. False — снимать нечего."""
    async with SessionFactory() as session, session.begin():
        row = (
            await session.execute(
                select(Task.id.label("task_id"), TaskAttempt.id.label("attempt_id"))
                .join(TaskAttempt, TaskAttempt.task_id == Task.id)
                .where(
                    TaskAttempt.status.in_(WAITING_ATTEMPT_STATUSES),
                    TaskAttempt.deadline_at < orchestrator.utcnow(),
                )
                .order_by(TaskAttempt.deadline_at)
                .limit(1)
                .with_for_update(of=Task, skip_locked=True)
            )
        ).one_or_none()
        if row is None:
            return False

        task = await orchestrator.lock_task(session, row.task_id)
        attempt = await orchestrator.lock_attempt(session, row.attempt_id)
        assert task is not None and attempt is not None
        if attempt.status == "submitted":
            detail = f"no webhook within {get_settings().provider_timeout_seconds:g}s"
        else:
            detail = "dispatch lease expired"
        next_attempt = await orchestrator.fail_locked(
            session, task, attempt, error_type="technical", detail=detail, final_status="timed_out"
        )
        if attempt.status in WAITING_ATTEMPT_STATUSES:
            # Задача уже завершена, а попытка почему-то активна — снимаем, чтобы не крутиться на ней.
            attempt.status = "cancelled"
            attempt.finished_at = orchestrator.utcnow()

    dispatcher.schedule(next_attempt)
    return True


async def orphaned_pending() -> list[int]:
    grace = timedelta(seconds=get_settings().pending_dispatch_grace_seconds)
    async with SessionFactory() as session:
        return list(
            await session.scalars(
                select(TaskAttempt.id)
                .where(
                    TaskAttempt.status == "pending",
                    TaskAttempt.created_at < orchestrator.utcnow() - grace,
                )
                .limit(BATCH)
            )
        )
