"""Единственное место, где меняется баланс.

Каждое изменение — строка в ledger_entries с уникальным idempotency_key и сдвиг
users.balance в той же транзакции. Поэтому баланс всегда равен сумме журнала,
а повтор операции с тем же ключом ничего не меняет.
"""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LedgerEntry, User


async def apply_entry(
    session: AsyncSession,
    *,
    user_id: int,
    amount: int,
    kind: str,
    idempotency_key: str,
    task_id: UUID | None = None,
    payment_id: str | None = None,
) -> LedgerEntry | None:
    """Записывает операцию и двигает баланс. Возвращает None, если операция с таким
    ключом уже есть. Вызывать внутри транзакции вызывающего.

    Конкурентные вставки одного ключа сериализует уникальный индекс: вторая ждёт
    коммита первой и получает конфликт, а при откате первой вставляет сама.
    """
    entry_id = await session.scalar(
        insert(LedgerEntry)
        .values(
            user_id=user_id,
            amount=amount,
            kind=kind,
            idempotency_key=idempotency_key,
            task_id=task_id,
            payment_id=payment_id,
        )
        .on_conflict_do_nothing(index_elements=[LedgerEntry.idempotency_key])
        .returning(LedgerEntry.id)
    )
    if entry_id is None:
        return None

    # CHECK (balance >= 0) не даст уйти в минус: транзакция упадёт целиком вместе со строкой журнала.
    await session.execute(
        update(User).where(User.id == user_id).values(balance=User.balance + amount)
    )
    return await session.get(LedgerEntry, entry_id)


async def find_entry(session: AsyncSession, idempotency_key: str) -> LedgerEntry | None:
    return await session.scalar(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key)
    )
