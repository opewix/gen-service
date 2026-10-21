"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("balance", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("balance >= 0", name=op.f("ck_users_balance_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )

    op.create_table(
        "generation_models",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.CheckConstraint("price > 0", name=op.f("ck_generation_models_price_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_models")),
        sa.UniqueConstraint("slug", name=op.f("uq_generation_models_slug")),
    )

    op.create_table(
        "model_legs",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_ref_images", sa.Integer(), nullable=False),
        sa.Column("skip_on_moderation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("max_ref_images >= 0", name=op.f("ck_model_legs_max_ref_images_non_negative")),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["generation_models.id"],
            name=op.f("fk_model_legs_model_id_generation_models"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_legs")),
        sa.UniqueConstraint("model_id", "priority", name=op.f("uq_model_legs_model_id")),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("model_id", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "ref_images",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("current_leg_id", sa.Integer(), nullable=True),
        sa.Column("result_url", sa.Text(), nullable=True),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name=op.f("ck_tasks_status_valid"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_tasks_user_id_users")),
        sa.ForeignKeyConstraint(
            ["model_id"], ["generation_models.id"], name=op.f("fk_tasks_model_id_generation_models")
        ),
        sa.ForeignKeyConstraint(
            ["current_leg_id"], ["model_legs.id"], name=op.f("fk_tasks_current_leg_id_model_legs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
    )
    op.create_index(
        "ix_tasks_active_by_user",
        "tasks",
        ["user_id"],
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "task_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("leg_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("error_type", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("result_url", sa.Text(), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'submitted', 'completed', 'failed', 'timed_out')",
            name=op.f("ck_task_attempts_status_valid"),
        ),
        sa.CheckConstraint(
            "error_type IS NULL OR error_type IN ('technical', 'moderation', 'payload')",
            name=op.f("ck_task_attempts_error_type_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"], name=op.f("fk_task_attempts_task_id_tasks"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["leg_id"], ["model_legs.id"], name=op.f("fk_task_attempts_leg_id_model_legs")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_attempts")),
        sa.UniqueConstraint("provider", "external_id", name=op.f("uq_task_attempts_provider")),
    )
    op.create_index(op.f("ix_task_attempts_task_id"), "task_attempts", ["task_id"])
    op.create_index(
        "ix_task_attempts_waiting_deadline",
        "task_attempts",
        ["deadline_at"],
        postgresql_where=sa.text("status = 'submitted'"),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("payment_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_ledger_entries_amount_non_zero")),
        sa.CheckConstraint(
            "kind IN ('payment', 'charge', 'refund')", name=op.f("ck_ledger_entries_kind_valid")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_ledger_entries_user_id_users")),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], name=op.f("fk_ledger_entries_task_id_tasks")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_ledger_entries_idempotency_key")),
    )
    op.create_index("ix_ledger_entries_user_id_id", "ledger_entries", ["user_id", "id"])


def downgrade() -> None:
    op.drop_table("ledger_entries")
    op.drop_table("task_attempts")
    op.drop_table("tasks")
    op.drop_table("model_legs")
    op.drop_table("generation_models")
    op.drop_table("users")
