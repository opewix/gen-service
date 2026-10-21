"""Вебхуки провайдеров: переход по ногам, модерация, payload, дубли."""

import asyncio

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import Providers, assert_balance_matches_ledger, fund, signed, wait_for_task
from tests.test_generate import order


async def ledger_kinds(api: httpx.AsyncClient, user_id: int) -> list[str]:
    return [e["kind"] for e in (await api.get(f"/users/{user_id}/balance")).json()["ledger"]]


async def external_id_of(db: AsyncEngine, task_id: str, provider: str) -> str:
    async with db.connect() as conn:
        return await conn.scalar(
            text("SELECT external_id FROM task_attempts WHERE task_id = :t AND provider = :p"),
            {"t": task_id, "p": provider},
        )


async def test_first_leg_fails_task_completes_on_second(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    await providers.configure("alpha", failure_rate=1, failure_type="technical")
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id)

    assert task["status"] == "completed"
    assert task["current_provider"] == "beta"
    assert task["current_leg"] == {
        "provider": "beta",
        "priority": 2,
        "max_ref_images": 10,
        "skip_on_moderation": False,
    }
    assert task["result_url"].startswith("https://files.example/beta/")
    assert [(a["provider"], a["status"], a["error_type"]) for a in task["attempts"]] == [
        ("alpha", "failed", "technical"),
        ("beta", "completed", None),
    ]
    assert await assert_balance_matches_ledger(db, user_id) == 8
    assert await ledger_kinds(api, user_id) == ["charge", "payment"]


async def test_10_concurrent_failed_on_last_leg_refund_once(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    # alpha отказывает, beta (последняя нога) молчит — failed пришлём сами, 10 сразу.
    await providers.configure("alpha", failure_rate=1, failure_type="technical")
    await providers.configure("beta", drop_webhook_rate=1)
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    await wait_for_task(
        api, task_id, lambda t: len(t["attempts"]) == 2 and t["attempts"][1]["status"] == "submitted"
    )
    webhook = {
        "external_id": await external_id_of(db, task_id, "beta"),
        "status": "failed",
        "error_type": "technical",
    }

    responses = await asyncio.gather(
        *(api.post("/webhooks/providers/beta", **signed(webhook)) for _ in range(10))
    )

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert sorted(r.json()["result"] for r in responses) == ["accepted"] + ["ignored"] * 9
    task = (await api.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "failed"
    async with db.connect() as conn:
        refunds = await conn.scalar(
            text("SELECT count(*) FROM ledger_entries WHERE task_id = :t AND kind = 'refund'"),
            {"t": task_id},
        )
    assert refunds == 1
    assert await assert_balance_matches_ledger(db, user_id) == 10


async def test_payload_error_fails_immediately_without_trying_next_leg(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    await providers.configure("alpha", failure_rate=1, failure_type="payload")
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id)

    assert task["status"] == "failed"
    assert task["error_type"] == "payload"
    assert [a["provider"] for a in task["attempts"]] == ["alpha"]
    assert await assert_balance_matches_ledger(db, user_id) == 10


async def test_moderation_skips_skip_on_moderation_leg(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    # video-long: beta → alpha(skip_on_moderation). Отказ модерации на beta — alpha не пробуем.
    await providers.configure("beta", failure_rate=1, failure_type="moderation")
    await fund(api, user_id, 25)

    task_id = (await api.post("/generate", json=order(user_id, model="video-long"))).json()["task_id"]
    task = await wait_for_task(api, task_id)

    assert task["status"] == "failed"
    assert task["error_type"] == "moderation"
    assert [a["provider"] for a in task["attempts"]] == ["beta"]
    assert await assert_balance_matches_ledger(db, user_id) == 25


async def test_technical_failure_does_not_skip_skip_on_moderation_leg(
    api: httpx.AsyncClient, user_id: int, providers: Providers
) -> None:
    await providers.configure("beta", failure_rate=1, failure_type="technical")
    await fund(api, user_id, 25)

    task_id = (await api.post("/generate", json=order(user_id, model="video-long"))).json()["task_id"]
    task = await wait_for_task(api, task_id)

    assert task["status"] == "completed"
    assert [a["provider"] for a in task["attempts"]] == ["beta", "alpha"]


async def test_moderation_moves_to_regular_next_leg(
    api: httpx.AsyncClient, user_id: int, providers: Providers
) -> None:
    await providers.configure("alpha", failure_rate=1, failure_type="moderation")
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id)

    assert task["status"] == "completed"
    assert [(a["provider"], a["error_type"]) for a in task["attempts"]] == [
        ("alpha", "moderation"),
        ("beta", None),
    ]


async def test_leg_that_cannot_take_refs_is_skipped_on_failover(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    # video-long с 2 референсами: alpha берёт только 1, значит после beta идти некуда.
    await providers.configure("beta", failure_rate=1, failure_type="technical")
    await fund(api, user_id, 25)

    task_id = (await api.post("/generate", json=order(user_id, model="video-long", refs=2))).json()["task_id"]
    task = await wait_for_task(api, task_id)

    assert task["status"] == "failed"
    assert [a["provider"] for a in task["attempts"]] == ["beta"]
    assert await assert_balance_matches_ledger(db, user_id) == 25


async def test_duplicate_concurrent_completed_webhooks_are_harmless(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int, providers: Providers
) -> None:
    await providers.configure("alpha", duplicate_webhook="concurrent")
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id)
    await asyncio.sleep(1)  # дать второму вебхуку дойти

    task = (await api.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "completed"
    assert len(task["attempts"]) == 1
    assert await assert_balance_matches_ledger(db, user_id) == 8


async def test_duplicate_failed_webhooks_move_one_leg_only(
    api: httpx.AsyncClient, user_id: int, providers: Providers
) -> None:
    await providers.configure("alpha", failure_rate=1, duplicate_webhook="concurrent")
    await fund(api, user_id, 10)

    task_id = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    task = await wait_for_task(api, task_id)
    await asyncio.sleep(1)

    task = (await api.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "completed"
    assert [a["provider"] for a in task["attempts"]] == ["alpha", "beta"]


async def test_unknown_external_id_is_404_so_provider_retries(api: httpx.AsyncClient) -> None:
    webhook = {"external_id": "alpha-nope", "status": "completed", "result_url": "https://x/y.png"}
    r = await api.post("/webhooks/providers/alpha", **signed(webhook))
    assert r.status_code == 404


async def test_unknown_provider_is_404(api: httpx.AsyncClient) -> None:
    webhook = {"external_id": "x", "status": "completed", "result_url": "https://x/y.png"}
    assert (await api.post("/webhooks/providers/gamma", **signed(webhook))).status_code == 404


async def test_webhook_without_outcome_details_is_422(api: httpx.AsyncClient) -> None:
    for webhook in (
        {"external_id": "x", "status": "completed"},
        {"external_id": "x", "status": "failed"},
    ):
        assert (await api.post("/webhooks/providers/alpha", **signed(webhook))).status_code == 422
