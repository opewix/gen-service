"""Тесты ходят в живой стек из docker compose: API, фейковых провайдеров и базу."""

import asyncio
import json
import os
import random
from collections.abc import AsyncIterator
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings
from app.signing import sign

API_URL = os.environ.get("API_URL", "http://localhost:8000")
PROVIDER_URLS = {
    "alpha": os.environ.get("ALPHA_URL", "http://localhost:8001"),
    "beta": os.environ.get("BETA_URL", "http://localhost:8002"),
}
# Быстрые и безотказные фейки по умолчанию: каждый тест включает только тот сбой,
# который проверяет, и не ждёт по 2–20 секунд.
TEST_BEHAVIOR = {"delay_min": 0.2, "delay_max": 0.5, "webhook_retry_delay": 0.3}


class Providers:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def configure(self, name: str, **overrides: Any) -> None:
        response = await self._client.put(
            f"{PROVIDER_URLS[name]}/control/behavior", json={**TEST_BEHAVIOR, **overrides}
        )
        response.raise_for_status()


@pytest.fixture(autouse=True)
async def providers() -> AsyncIterator[Providers]:
    async with httpx.AsyncClient(timeout=10) as client:
        providers = Providers(client)
        for name in PROVIDER_URLS:
            await providers.configure(name)
        yield providers


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=API_URL, timeout=10) as client:
        yield client


@pytest.fixture
async def db() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
def user_id() -> int:
    # Свой пользователь на каждый тест: тесты не чистят базу и не мешают друг другу.
    return random.randint(10**9, 2**62)


def signed(payload: dict[str, Any]) -> dict[str, Any]:
    """Аргументы для client.post: тело и подпись X-Signature от тех же байтов."""
    body = json.dumps(payload).encode()
    return {
        "content": body,
        "headers": {
            "Content-Type": "application/json",
            "X-Signature": sign(body, get_settings().webhook_secret),
        },
    }


async def assert_balance_matches_ledger(db: AsyncEngine, user_id: int) -> int:
    async with db.connect() as conn:
        balance, ledger_sum = (
            await conn.execute(
                text(
                    """
                    SELECT u.balance, COALESCE(SUM(l.amount), 0)
                    FROM users u LEFT JOIN ledger_entries l ON l.user_id = u.id
                    WHERE u.id = :user_id
                    GROUP BY u.balance
                    """
                ),
                {"user_id": user_id},
            )
        ).one()
    assert balance == ledger_sum
    return balance


async def fund(api: httpx.AsyncClient, user_id: int, tokens: int) -> None:
    """Пополняет баланс через платёжный вебхук — тем же путём, что и в жизни."""
    body = {"payment_id": f"test-{uuid4()}", "user_id": user_id, "tokens": tokens, "status": "succeeded"}
    response = await api.post("/webhooks/payments", **signed(body))
    assert response.json() == {"result": "credited"}


async def balance_of(api: httpx.AsyncClient, user_id: int) -> int:
    return (await api.get(f"/users/{user_id}/balance")).json()["balance"]



async def wait_for_task(
    api: httpx.AsyncClient,
    task_id: str,
    until: Callable[[dict], bool] = lambda t: t["status"] in ("completed", "failed"),
    timeout: float = 30,
) -> dict:
    """Опрашивает GET /tasks/{id}, пока задача не придёт в нужное состояние."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        task = (await api.get(f"/tasks/{task_id}")).json()
        if until(task):
            return task
        if loop.time() > deadline:
            raise AssertionError(f"task did not reach expected state in {timeout}s: {task}")
        await asyncio.sleep(0.2)
