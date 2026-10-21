from typing import Literal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ledger import apply_entry, find_entry
from app.models import User
from app.schemas import PaymentWebhook

# Деньги зачисляем только по подтверждённой оплате; pending, failed и прочие
# статусы принимаем (иначе платёжка будет ретраить), но баланс не трогаем.
SUCCESS_STATUS = "succeeded"

PaymentResult = Literal["credited", "duplicate", "ignored", "conflict"]


async def process_payment(session: AsyncSession, payment: PaymentWebhook) -> PaymentResult:
    if payment.status != SUCCESS_STATUS:
        return "ignored"

    key = f"payment:{payment.payment_id}"
    async with session.begin():
        # Регистрации нет, а оплата — реальные деньги: пользователя, о котором знает
        # платёжка, заводим сами, а не отвечаем 404 и не теряем платёж.
        await session.execute(
            insert(User).values(id=payment.user_id).on_conflict_do_nothing(index_elements=[User.id])
        )
        entry = await apply_entry(
            session,
            user_id=payment.user_id,
            amount=payment.tokens,
            kind="payment",
            idempotency_key=key,
            payment_id=payment.payment_id,
        )
        if entry is not None:
            return "credited"

        # Тот же payment_id уже зачислен. Если повтор совпадает — это обычный дубль;
        # если сумма или пользователь другие — второй раз не зачисляем и сообщаем.
        existing = await find_entry(session, key)
        assert existing is not None
        if existing.user_id != payment.user_id or existing.amount != payment.tokens:
            return "conflict"
        return "duplicate"
