import json

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.signing import sign, verify


async def test_health(api: httpx.AsyncClient) -> None:
    response = await api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_catalog_seeded_by_migration(db: AsyncEngine) -> None:
    async with db.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    """
                    SELECT m.slug, m.price, l.provider, l.max_ref_images, l.skip_on_moderation
                    FROM generation_models m JOIN model_legs l ON l.model_id = m.id
                    ORDER BY m.slug, l.priority
                    """
                )
            )
        ).all()
    assert [tuple(r) for r in rows] == [
        ("image-fast", 2, "alpha", 4, False),
        ("image-fast", 2, "beta", 10, False),
        ("video-long", 25, "beta", 2, False),
        ("video-long", 25, "alpha", 1, True),
    ]


async def test_webhook_rejects_bad_signature(api: httpx.AsyncClient) -> None:
    body = json.dumps({"payment_id": "p1", "user_id": 1, "tokens": 10, "status": "succeeded"})
    response = await api.post(
        "/webhooks/payments", content=body, headers={"X-Signature": "wrong"}
    )
    assert response.status_code == 401


async def test_webhook_accepts_valid_signature(api: httpx.AsyncClient) -> None:
    body = json.dumps({"payment_id": "p1", "user_id": 1, "tokens": 10, "status": "succeeded"})
    response = await api.post(
        "/webhooks/payments",
        content=body,
        headers={"X-Signature": sign(body.encode(), get_settings().webhook_secret)},
    )
    # Подпись прошла; сама обработка пока заглушка.
    assert response.status_code == 501


def test_sign_verify_roundtrip() -> None:
    body = b'{"a": 1}'
    signature = sign(body, "secret")
    assert verify(body, signature, "secret")
    assert not verify(body + b" ", signature, "secret")
    assert not verify(body, None, "secret")
