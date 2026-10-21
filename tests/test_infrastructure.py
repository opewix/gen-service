import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

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


def test_sign_verify_roundtrip() -> None:
    body = b'{"a": 1}'
    signature = sign(body, "secret")
    assert verify(body, signature, "secret")
    assert not verify(body + b" ", signature, "secret")
    assert not verify(body, None, "secret")
