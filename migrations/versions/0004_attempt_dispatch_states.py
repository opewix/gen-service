"""attempt dispatch states: dispatching and cancelled

dispatching — попытку забрал отправщик и шлёт POST /tasks. deadline_at в этом
статусе — аренда: если процесс упал посреди отправки, сторож увидит истёкшую
аренду и поведёт задачу дальше.
cancelled — попытка снята, потому что задача уже завершилась на другой ноге
(поздний completed, правило 6).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "status IN ('pending', 'submitted', 'completed', 'failed', 'timed_out')"
NEW_STATUSES = (
    "status IN ('pending', 'dispatching', 'submitted', 'completed', 'failed', 'timed_out', 'cancelled')"
)


def upgrade() -> None:
    op.drop_constraint(op.f("ck_task_attempts_status_valid"), "task_attempts", type_="check")
    op.create_check_constraint(op.f("ck_task_attempts_status_valid"), "task_attempts", NEW_STATUSES)

    # Сторож следит за дедлайнами и отправляемых, и отправленных попыток.
    op.drop_index("ix_task_attempts_waiting_deadline", table_name="task_attempts")
    op.create_index(
        "ix_task_attempts_waiting_deadline",
        "task_attempts",
        ["deadline_at"],
        postgresql_where=sa.text("status IN ('dispatching', 'submitted')"),
    )
    # И подбирает pending-попытки, которые никто не отправил (процесс упал после коммита).
    op.create_index(
        "ix_task_attempts_pending_created",
        "task_attempts",
        ["created_at"],
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("ix_task_attempts_pending_created", table_name="task_attempts")
    op.drop_index("ix_task_attempts_waiting_deadline", table_name="task_attempts")
    op.create_index(
        "ix_task_attempts_waiting_deadline",
        "task_attempts",
        ["deadline_at"],
        postgresql_where=sa.text("status = 'submitted'"),
    )
    # Новых статусов в старом CHECK нет: такие попытки считаем неудачными.
    op.execute(
        "UPDATE task_attempts SET status = 'failed' WHERE status IN ('dispatching', 'cancelled')"
    )
    op.drop_constraint(op.f("ck_task_attempts_status_valid"), "task_attempts", type_="check")
    op.create_check_constraint(op.f("ck_task_attempts_status_valid"), "task_attempts", OLD_STATUSES)
