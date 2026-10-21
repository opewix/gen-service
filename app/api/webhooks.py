from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import dispatcher
from app.api.deps import signed_body
from app.config import get_settings
from app.db import get_session
from app.orchestrator import process_provider_webhook
from app.payments import process_payment
from app.schemas import PaymentWebhook, PaymentWebhookOut, ProviderWebhook, ProviderWebhookOut

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/providers/{name}", response_model=ProviderWebhookOut, responses={404: {}})
async def provider_webhook(
    name: str, body: bytes = Depends(signed_body), session: AsyncSession = Depends(get_session)
) -> ProviderWebhookOut:
    if name not in get_settings().provider_urls:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown provider")
    result, next_attempt = await process_provider_webhook(
        session, name, ProviderWebhook.model_validate_json(body)
    )
    if result == "unknown":
        # Не 200: провайдер повторит, и к тому времени external_id уже будет записан.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown external_id")
    # Следующая нога уже закоммичена как pending; если процесс упадёт до отправки,
    # её подберёт сторож.
    dispatcher.schedule(next_attempt)
    return ProviderWebhookOut(result=result)


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
