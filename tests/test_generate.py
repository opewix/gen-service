import asyncio

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import assert_balance_matches_ledger, balance_of, fund


def order(user_id: int, model: str = "image-fast", refs: int = 0) -> dict:
    return {
        "user_id": user_id,
        "model_slug": model,
        "prompt": "a cat in space",
        "ref_images": [f"https://refs.example/{i}.png" for i in range(refs)],
    }


async def test_generate_charges_price_and_creates_task(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    await fund(api, user_id, 10)

    r = await api.post("/generate", json=order(user_id))
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]

    assert await assert_balance_matches_ledger(db, user_id) == 8
    ledger = (await api.get(f"/users/{user_id}/balance")).json()["ledger"]
    assert ledger[0] == {**ledger[0], "kind": "charge", "amount": -2, "task_id": task_id}

    task = (await api.get(f"/tasks/{task_id}")).json()
    assert task["model_slug"] == "image-fast"
    assert task["attempts"][0]["provider"] == "alpha"


async def test_first_leg_that_accepts_refs_is_chosen(api: httpx.AsyncClient, user_id: int) -> None:
    await fund(api, user_id, 10)
    # alpha принимает до 4 референсов, beta до 10.
    r = await api.post("/generate", json=order(user_id, refs=5))
    task = (await api.get(f"/tasks/{r.json()['task_id']}")).json()
    assert [a["provider"] for a in task["attempts"]] == ["beta"]


async def test_no_leg_accepts_refs_is_400_before_charge(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    await fund(api, user_id, 10)
    r = await api.post("/generate", json=order(user_id, refs=11))
    assert r.status_code == 400
    assert await assert_balance_matches_ledger(db, user_id) == 10


async def test_insufficient_tokens_is_402(api: httpx.AsyncClient, user_id: int) -> None:
    await fund(api, user_id, 24)
    r = await api.post("/generate", json=order(user_id, model="video-long"))
    assert r.status_code == 402
    assert await balance_of(api, user_id) == 24


async def test_unknown_model_or_user_is_404(api: httpx.AsyncClient, user_id: int) -> None:
    assert (await api.post("/generate", json=order(user_id))).status_code == 404
    await fund(api, user_id, 10)
    assert (await api.post("/generate", json=order(user_id, model="nope"))).status_code == 404


async def test_concurrent_generates_respect_active_limit(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    await fund(api, user_id, 100)

    responses = await asyncio.gather(*(api.post("/generate", json=order(user_id)) for _ in range(10)))

    codes = sorted(r.status_code for r in responses)
    assert codes == [202] * 3 + [429] * 7
    assert await assert_balance_matches_ledger(db, user_id) == 100 - 3 * 2


async def test_unknown_task_is_404(api: httpx.AsyncClient) -> None:
    r = await api.get("/tasks/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
