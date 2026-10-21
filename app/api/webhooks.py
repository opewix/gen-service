from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import signed_body
from app.db import get_session
from app.payments import process_payment
from app.schemas import PaymentWebhook, PaymentWebhookOut, ProviderWebhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/providers/{name}")
async def provider_webhook(name: str, body: bytes = Depends(signed_body)) -> None:
    ProviderWebhook.model_validate_json(body)
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "not implemented")


@router.post("/payments", response_model=PaymentWebhookOut, responses={409: {}})
async def payment_webhook(
    body: bytes = Depends(signed_body), session: AsyncSession = Depends(get_session)
) -> PaymentWebhookOut:
    # Дубль отвечает 200, как и первое зачисление: для платёжки это «доставлено»,
    # и она перестанет ретраить.
    result = await process_payment(session, PaymentWebhook.model_validate_json(body))
    if result == "conflict":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "payment_id already credited with different user or amount"
        )
    return PaymentWebhookOut(result=result)
