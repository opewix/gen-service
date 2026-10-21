"""Эндпоинты и фейки, на которые опирается UI."""

import asyncio
import os

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import assert_balance_matches_ledger, fund, wait_for_task
from tests.test_generate import order

PAYMENTS_URL = os.environ.get("PAYMENTS_URL", "http://localhost:8003")


async def test_models_list_catalog_with_legs(api: httpx.AsyncClient) -> None:
    models = {m["slug"]: m for m in (await api.get("/models")).json()}
    assert models["image-fast"]["price"] == 2
    assert [(l["provider"], l["max_ref_images"]) for l in models["image-fast"]["legs"]] == [
        ("alpha", 4),
        ("beta", 10),
    ]
    assert models["video-long"]["legs"][1]["skip_on_moderation"] is True


async def test_user_tasks_newest_first(api: httpx.AsyncClient, user_id: int) -> None:
    await fund(api, user_id, 10)
    first = (await api.post("/generate", json=order(user_id))).json()["task_id"]
    second = (await api.post("/generate", json={**order(user_id), "prompt": "second"})).json()["task_id"]
    await wait_for_task(api, second)

    tasks = (await api.get(f"/users/{user_id}/tasks")).json()
    assert [t["id"] for t in tasks] == [second, first]
    assert tasks[0]["prompt"] == "second"
    assert tasks[0]["attempts"][0]["provider"] == "alpha"


async def test_user_tasks_of_unknown_user_is_404(api: httpx.AsyncClient, user_id: int) -> None:
    assert (await api.get(f"/users/{user_id}/tasks")).status_code == 404


async def test_user_without_tasks_gets_empty_list(api: httpx.AsyncClient, user_id: int) -> None:
    await fund(api, user_id, 1)
    assert (await api.get(f"/users/{user_id}/tasks")).json() == []


async def test_ui_is_served(api: httpx.AsyncClient) -> None:
    root = await api.get("/", follow_redirects=True)
    assert root.status_code == 200
    assert "gen·service" in root.text


async def test_fake_payments_duplicated_webhooks_credit_once(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    async with httpx.AsyncClient(base_url=PAYMENTS_URL) as payments:
        r = await payments.post("/checkout", json={"user_id": user_id, "tokens": 30, "duplicates": 5})
    assert r.status_code == 202

    for _ in range(50):
        await asyncio.sleep(0.2)
        if (await api.get(f"/users/{user_id}/balance")).status_code == 200:
            break
    await asyncio.sleep(1)  # дать дойти остальным дублям
    assert await assert_balance_matches_ledger(db, user_id) == 30
