"""Сторож: молчащий провайдер, упавший процесс и поздний completed (правило 6).

Тесты правила 6 ждут PROVIDER_TIMEOUT по-настоящему (по умолчанию 60 с).
Для быстрого прогона: PROVIDER_TIMEOUT_SECONDS=5 docker compose up -d.
"""

from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from tests.conftest import Providers, assert_balance_matches_ledger, fund, wait_for_task
from tests.test_failover import ledger_kinds
from tests.test_generate import order

TIMEOUT = get_settings().provider_timeout_seconds
# Запас на интервал сторожа, отправку и опрос.
SLACK = 10


async def test_late_completed_while_next_leg_runs_completes_task_without_refund(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    # alpha ответит через TIMEOUT+3 с — уже после того, как сторож уведёт задачу на beta.
    # beta молчит, так что поздний completed от alpha застаёт задачу в работе.
    await providers.configure("alpha", delay_min=TIMEOUT + 3, delay_max=TIMEOUT + 3)
    await providers.configure("beta", drop_webhook_rate=1)
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id, timeout=TIMEOUT + 3 + SLACK)

    assert task["status"] == "completed"
    assert task["result_url"].startswith("https://files.example/alpha/")
    alpha, beta = task["attempts"]
    assert (alpha["status"], alpha["error_type"]) == ("timed_out", "technical")
    assert alpha["late_result_at"] is not None
    assert alpha["result_url"] == task["result_url"]
    assert beta["status"] == "cancelled"
    # Результат есть — значит возврата нет: списание осталось.
    assert await ledger_kinds(api, user_id) == ["charge", "payment"]
    assert await assert_balance_matches_ledger(db, user_id) == 8


async def test_late_completed_after_refund_does_not_deliver_result(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    # video-long с 2 референсами принимает только beta: одна нога, после неё — возврат.
    await providers.configure("beta", delay_min=TIMEOUT + 3, delay_max=TIMEOUT + 3)
    await fund(api, user_id, 25)

    task_id = (await api.post("/generate", json=order(user_id, model="video-long", refs=2))).json()[
        "task_id"
    ]
    task = await wait_for_task(api, task_id, timeout=TIMEOUT + SLACK)
    assert task["status"] == "failed"
    assert await assert_balance_matches_ledger(db, user_id) == 25

    task = await wait_for_task(
        api, task_id, lambda t: t["attempts"][0]["late_result_at"] is not None, timeout=3 + SLACK
    )
    # Возврат уже был — результат не выдаём, второй раз не списываем.
    assert task["status"] == "failed"
    assert task["result_url"] is None
    assert task["attempts"][0]["status"] == "timed_out"
    assert task["attempts"][0]["result_url"] is not None
    assert await ledger_kinds(api, user_id) == ["refund", "charge", "payment"]
    assert await assert_balance_matches_ledger(db, user_id) == 25


async def insert_crashed_task(db: AsyncEngine, user_id: int, attempt_status: str) -> str:
    """Задача в том виде, в каком её оставил процесс, упавший до или во время отправки."""
    task_id = str(uuid4())
    async with db.begin() as conn:
        leg_id, model_id = (
            await conn.execute(
                text(
                    """
                    SELECT l.id, m.id FROM model_legs l JOIN generation_models m ON m.id = l.model_id
                    WHERE m.slug = 'image-fast' AND l.provider = 'alpha'
                    """
                )
            )
        ).one()
        await conn.execute(
            text(
                """
                INSERT INTO tasks (id, user_id, model_id, prompt, price, status, current_leg_id)
                VALUES (:id, :user_id, :model_id, 'crash', 2, 'pending', :leg_id)
                """
            ),
            {"id": task_id, "user_id": user_id, "model_id": model_id, "leg_id": leg_id},
        )
        await conn.execute(
            text(
                """
                INSERT INTO task_attempts (task_id, leg_id, provider, status, created_at, deadline_at)
                VALUES (:task_id, :leg_id, 'alpha', :status,
                        now() - interval '1 minute', now() - interval '1 second')
                """
            ),
            {"task_id": task_id, "leg_id": leg_id, "status": attempt_status},
        )
    return task_id


async def test_attempt_left_pending_by_crashed_process_is_dispatched(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    await fund(api, user_id, 10)
    task_id = await insert_crashed_task(db, user_id, "pending")

    task = await wait_for_task(api, task_id, timeout=SLACK)

    assert task["status"] == "completed"
    assert [(a["provider"], a["status"]) for a in task["attempts"]] == [("alpha", "completed")]


async def test_crash_mid_dispatch_moves_to_next_leg_when_lease_expires(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    await fund(api, user_id, 10)
    task_id = await insert_crashed_task(db, user_id, "dispatching")

    task = await wait_for_task(api, task_id, timeout=SLACK)

    assert task["status"] == "completed"
    alpha, beta = task["attempts"]
    assert (alpha["status"], alpha["error_detail"]) == ("timed_out", "dispatch lease expired")
    assert (beta["provider"], beta["status"]) == ("beta", "completed")
