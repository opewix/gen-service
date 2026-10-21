"""ORM-модели. Схему меняют только миграции (migrations/versions), а эти классы
должны с ними совпадать: `alembic check` покажет расхождение.

Статусы хранятся в text + CHECK, а не в PG ENUM: добавить значение в CHECK
можно обычной миграцией, а ALTER TYPE ... ADD VALUE нельзя откатить.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TASK_STATUSES = ("pending", "running", "completed", "failed")
ACTIVE_TASK_STATUSES = ("pending", "running")
ATTEMPT_STATUSES = (
    "pending",
    "dispatching",
    "submitted",
    "completed",
    "failed",
    "timed_out",
    "cancelled",
)
ACTIVE_ATTEMPT_STATUSES = ("pending", "dispatching", "submitted")
# Попытки, у которых идёт дедлайн: аренда отправки или ожидание вебхука.
WAITING_ATTEMPT_STATUSES = ("dispatching", "submitted")
ERROR_TYPES = ("technical", "moderation", "payload")
LEDGER_KINDS = ("payment", "charge", "refund")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("balance >= 0", name="balance_non_negative"),)

    # id приходит извне (регистрации нет), поэтому без автогенерации.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # Кэш суммы журнала: меняется только в той же транзакции, что и вставка в ledger_entries.
    balance: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GenerationModel(Base):
    __tablename__ = "generation_models"
    __table_args__ = (CheckConstraint("price > 0", name="price_positive"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    price: Mapped[int] = mapped_column(Integer)


class ModelLeg(Base):
    __tablename__ = "model_legs"
    __table_args__ = (
        UniqueConstraint("model_id", "priority"),
        CheckConstraint("max_ref_images >= 0", name="max_ref_images_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("generation_models.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer)
    max_ref_images: Mapped[int] = mapped_column(Integer)
    skip_on_moderation: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_in("status", TASK_STATUSES), name="status_valid"),
        # Для проверки «не больше N активных задач» на пользователя.
        Index(
            "ix_tasks_active_by_user",
            "user_id",
            postgresql_where=text(_in("status", ACTIVE_TASK_STATUSES)),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    model_id: Mapped[int] = mapped_column(ForeignKey("generation_models.id"))
    prompt: Mapped[str] = mapped_column(Text)
    ref_images: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    # Цена на момент заказа: возвращаем ровно то, что списали, даже если прайс поменяли.
    price: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    current_leg_id: Mapped[int | None] = mapped_column(ForeignKey("model_legs.id"))
    result_url: Mapped[str | None] = mapped_column(Text)
    error_type: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TaskAttempt(Base):
    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint("provider", "external_id"),
        CheckConstraint(_in("status", ATTEMPT_STATUSES), name="status_valid"),
        CheckConstraint(
            f"error_type IS NULL OR {_in('error_type', ERROR_TYPES)}", name="error_type_valid"
        ),
        # Сторож ищет попытки с истёкшим дедлайном и брошенные pending.
        Index(
            "ix_task_attempts_waiting_deadline",
            "deadline_at",
            postgresql_where=text(_in("status", WAITING_ATTEMPT_STATUSES)),
        ),
        Index(
            "ix_task_attempts_pending_created",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    leg_id: Mapped[int] = mapped_column(ForeignKey("model_legs.id"))
    provider: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    error_type: Mapped[str | None] = mapped_column(Text)
    error_detail: Mapped[str | None] = mapped_column(Text)
    result_url: Mapped[str | None] = mapped_column(Text)
    # completed пришёл после того, как сторож перестал ждать эту попытку (правило 6).
    late_result_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_non_zero"),
        CheckConstraint(_in("kind", LEDGER_KINDS), name="kind_valid"),
        Index("ix_ledger_entries_user_id_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # Со знаком: + начисление и возврат, - списание.
    amount: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    # Основа «ровно один раз»: charge:<task_id>, refund:<task_id>, payment:<payment_id>.
    # Повтор той же операции упирается в UNIQUE и не проходит.
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"))
    payment_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
