from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import LedgerEntry, User
from app.schemas import BalanceOut, LedgerEntryOut

router = APIRouter(tags=["users"])

LEDGER_TAIL = 20


@router.get("/users/{user_id}/balance", response_model=BalanceOut, responses={404: {}})
async def get_balance(user_id: int, session: AsyncSession = Depends(get_session)) -> BalanceOut:
    # Одна транзакция REPEATABLE READ: баланс и строки журнала из одного снимка,
    # иначе между двумя SELECT могла бы вклиниться новая операция.
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")

    entries = await session.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.user_id == user_id)
        .order_by(LedgerEntry.id.desc())
        .limit(LEDGER_TAIL)
    )
    return BalanceOut(
        user_id=user.id,
        balance=user.balance,
        ledger=[
            LedgerEntryOut(
                amount=e.amount,
                kind=e.kind,
                task_id=e.task_id,
                payment_id=e.payment_id,
                created_at=e.created_at,
            )
            for e in entries
        ],
    )
