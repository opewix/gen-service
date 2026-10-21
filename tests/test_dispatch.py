"""Синхронные ошибки на POST /tasks: 503 и таймаут ведут на следующую ногу."""

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from tests.conftest import Providers, assert_balance_matches_ledger, fund, wait_for_task
from tests.test_generate import order


def moved_past_first_leg(task: dict) -> bool:
    return len(task["attempts"]) == 2 and task["attempts"][1]["status"] not in ("pending", "dispatching")


async def test_sync_503_moves_to_next_leg(
    api: httpx.AsyncClient, user_id: int, providers: Providers
) -> None:
    await providers.configure("alpha", sync_failure_rate=1, sync_failure="503")
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id, moved_past_first_leg)

    first, second = task["attempts"]
    assert (first["provider"], first["status"], first["error_type"]) == ("alpha", "failed", "technical")
    assert first["error_detail"] == "sync error: HTTP 503"
    assert second["provider"] == "beta"


async def test_sync_timeout_moves_to_next_leg(
    api: httpx.AsyncClient, user_id: int, providers: Providers
) -> None:
    request_timeout = get_settings().provider_request_timeout_seconds
    await providers.configure(
        "alpha", sync_failure_rate=1, sync_failure="timeout", sync_timeout_seconds=request_timeout + 2
    )
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id, moved_past_first_leg, timeout=request_timeout + 10)

    first, second = task["attempts"]
    assert (first["status"], first["error_type"]) == ("failed", "technical")
    assert first["error_detail"] == "sync error: ReadTimeout"
    assert second["provider"] == "beta"


async def test_sync_failure_on_every_leg_refunds_once(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    for name in ("alpha", "beta"):
        await providers.configure(name, sync_failure_rate=1, sync_failure="503")
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id)

    assert task["status"] == "failed"
    assert task["error_type"] == "technical"
    assert [a["provider"] for a in task["attempts"]] == ["alpha", "beta"]
    assert await assert_balance_matches_ledger(db, user_id) == 10
    kinds = [e["kind"] for e in (await api.get(f"/users/{user_id}/balance")).json()["ledger"]]
    assert kinds == ["refund", "charge", "payment"]
