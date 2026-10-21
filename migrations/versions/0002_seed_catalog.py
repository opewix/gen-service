"""seed catalog: image-fast, video-long and their legs

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLUGS = ("image-fast", "video-long")


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO generation_models (slug, price) VALUES
            ('image-fast', 2),
            ('video-long', 25)
        """
    )
    op.execute(
        """
        INSERT INTO model_legs (model_id, provider, priority, max_ref_images, skip_on_moderation)
        SELECT m.id, v.provider, v.priority, v.max_ref_images, v.skip_on_moderation
        FROM (VALUES
            ('image-fast', 'alpha', 1, 4,  false),
            ('image-fast', 'beta',  2, 10, false),
            ('video-long', 'beta',  1, 2,  false),
            ('video-long', 'alpha', 2, 1,  true)
        ) AS v(slug, provider, priority, max_ref_images, skip_on_moderation)
        JOIN generation_models m ON m.slug = v.slug
        """
    )


def downgrade() -> None:
    # model_legs удаляются каскадом.
    op.execute(f"DELETE FROM generation_models WHERE slug IN {SLUGS!r}")
