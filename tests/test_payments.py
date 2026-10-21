import asyncio
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.conftest import assert_balance_matches_ledger, signed


def payment(user_id: int, tokens: int = 10, status: str = "succeeded", payment_id: str | None = None):
    return {
        "payment_id": payment_id or str(uuid4()),
        "user_id": user_id,
        "tokens": tokens,
        "status": status,
    }


async def test_20_concurrent_identical_payments_credit_once(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    body = payment(user_id, tokens=50)

    responses = await asyncio.gather(
        *(api.post("/webhooks/payments", **signed(body)) for _ in range(20))
    )

    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    results = sorted(r.json()["result"] for r in responses)
    assert results == ["credited"] + ["duplicate"] * 19

    async with db.connect() as conn:
        rows = await conn.scalar(
            text("SELECT count(*) FROM ledger_entries WHERE payment_id = :p"),
            {"p": body["payment_id"]},
        )
    assert rows == 1
    assert await assert_balance_matches_ledger(db, user_id) == 50


async def test_different_payments_both_credited(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    for tokens in (10, 15):
        r = await api.post("/webhooks/payments", **signed(payment(user_id, tokens=tokens)))
        assert r.json() == {"result": "credited"}

    assert await assert_balance_matches_ledger(db, user_id) == 25


async def test_not_succeeded_payment_is_acknowledged_but_not_credited(
    api: httpx.AsyncClient, user_id: int
) -> None:
    r = await api.post("/webhooks/payments", **signed(payment(user_id, status="pending")))
    assert r.status_code == 200
    assert r.json() == {"result": "ignored"}

    # Пользователь не создан: по неподтверждённой оплате ничего не меняем.
    assert (await api.get(f"/users/{user_id}/balance")).status_code == 404


async def test_pending_then_succeeded_credits_once(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    body = payment(user_id, tokens=7)
    await api.post("/webhooks/payments", **signed({**body, "status": "pending"}))
    r = await api.post("/webhooks/payments", **signed(body))
    assert r.json() == {"result": "credited"}
    assert await assert_balance_matches_ledger(db, user_id) == 7


async def test_same_payment_id_with_other_amount_is_conflict(
    api: httpx.AsyncClient, db: AsyncEngine, user_id: int
) -> None:
    body = payment(user_id, tokens=10)
    await api.post("/webhooks/payments", **signed(body))

    r = await api.post("/webhooks/payments", **signed({**body, "tokens": 999}))
    assert r.status_code == 409
    assert await assert_balance_matches_ledger(db, user_id) == 10


async def test_bad_signature_rejected(api: httpx.AsyncClient, user_id: int) -> None:
    kwargs = signed(payment(user_id))
    kwargs["headers"]["X-Signature"] = "wrong"
    r = await api.post("/webhooks/payments", **kwargs)
    assert r.status_code == 401
    assert (await api.get(f"/users/{user_id}/balance")).status_code == 404


async def test_invalid_payload_rejected(api: httpx.AsyncClient, user_id: int) -> None:
    r = await api.post("/webhooks/payments", **signed(payment(user_id, tokens=-5)))
    assert r.status_code == 422


async def test_balance_shows_latest_ledger_entries_first(
    api: httpx.AsyncClient, user_id: int
) -> None:
    for tokens in (1, 2, 3):
        await api.post("/webhooks/payments", **signed(payment(user_id, tokens=tokens)))

    r = await api.get(f"/users/{user_id}/balance")
    assert r.status_code == 200
    data = r.json()
    assert data["balance"] == 6
    assert [e["amount"] for e in data["ledger"]] == [3, 2, 1]
    assert all(e["kind"] == "payment" for e in data["ledger"])


async def test_balance_of_unknown_user_is_404(api: httpx.AsyncClient, user_id: int) -> None:
    assert (await api.get(f"/users/{user_id}/balance")).status_code == 404
