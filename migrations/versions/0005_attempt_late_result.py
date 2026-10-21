"""attempt late result

Отметка о completed, пришедшем после того, как сторож перестал ждать попытку
(правило 6). Сам результат пишется в task_attempts.result_url, а эта колонка
показывает в истории, что он опоздал.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task_attempts", sa.Column("late_result_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("task_attempts", "late_result_at")
