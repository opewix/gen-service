"""seed demo users with zero balance

Регистрации в сервисе нет, а задачам и журналу нужен существующий пользователь.
Баланс 0 при пустом журнале: инвариант «баланс = сумма журнала» соблюдён.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("INSERT INTO users (id) VALUES (1), (2), (3)")


def downgrade() -> None:
    op.execute("DELETE FROM users WHERE id IN (1, 2, 3)")
