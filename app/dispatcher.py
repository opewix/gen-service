"""Отправка попыток провайдерам.

Попытка отправляется в фоне сразу после коммита, который её создал. Если процесс
упадёт раньше, pending-попытку подберёт сторож и вызовет тот же dispatch().
Захват попытки — атомарный UPDATE … WHERE status = 'pending', поэтому одну
попытку не отправят дважды, сколько бы процессов за ней ни пришло.
"""

import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import update

from app import orchestrator
from app.config import get_settings
from app.db import SessionFactory
from app.models import Task, TaskAttempt

log = logging.getLogger(__name__)

# Запас поверх таймаута запроса: аренда не должна истечь, пока POST ещё идёт.
LEASE_MARGIN_SECONDS = 10

_client: httpx.AsyncClient | None = None
_background: set[asyncio.Task[None]] = set()


def start() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=get_settings().provider_request_timeout_seconds)


async def stop() -> None:
    # Незавершённые отправки бросаем: их аренда истечёт, и сторож поведёт задачи дальше.
    for task in list(_background):
        task.cancel()
    if _client is not None:
        await _client.aclose()


def schedule(attempt_id: int | None) -> None:
    if attempt_id is None:
        return
    task = asyncio.create_task(_dispatch_logged(attempt_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _dispatch_logged(attempt_id: int) -> None:
    try:
        await dispatch(attempt_id)
    except Exception:
        # Попытка осталась в dispatching: сторож снимет её по истечении аренды.
        log.exception("dispatch of attempt %s failed", attempt_id)


async def dispatch(attempt_id: int) -> None:
    claimed = await _claim(attempt_id)
    if claimed is None:
        return
    task_id, provider, prompt, ref_images = claimed

    settings = get_settings()
    base_url = settings.provider_urls.get(provider)
    external_id: str | None = None
    failure: str | None = None
    if base_url is None:
        failure = f"unknown provider {provider!r}"
    else:
        assert _client is not None, "dispatcher.start() was not called"
        try:
            response = await _client.post(
                f"{base_url}/tasks", json={"prompt": prompt, "ref_images": ref_images}
            )
        except httpx.HTTPError as exc:
            failure = f"sync error: {type(exc).__name__}"
        else:
            if response.status_code == 202:
                external_id = response.json()["external_id"]
            else:
                failure = f"sync error: HTTP {response.status_code}"

    async with SessionFactory() as session:
        if external_id is not None:
            await orchestrator.record_submission(session, task_id, attempt_id, external_id)
            return
        # Синхронная ошибка (5xx, таймаут, обрыв) ведёт туда же, куда technical в вебхуке.
        next_attempt = await orchestrator.fail_attempt(
            session, task_id, attempt_id, error_type="technical", detail=failure
        )
    schedule(next_attempt)


async def _claim(attempt_id: int) -> tuple | None:
    lease = timedelta(seconds=get_settings().provider_request_timeout_seconds + LEASE_MARGIN_SECONDS)
    async with SessionFactory() as session, session.begin():
        row = (
            await session.execute(
                update(TaskAttempt)
                .where(TaskAttempt.id == attempt_id, TaskAttempt.status == "pending")
                .values(status="dispatching", deadline_at=orchestrator.utcnow() + lease)
                .returning(TaskAttempt.task_id, TaskAttempt.provider)
            )
        ).one_or_none()
        if row is None:
            return None
        task = await session.get(Task, row.task_id)
        assert task is not None
        return row.task_id, row.provider, task.prompt, task.ref_images
