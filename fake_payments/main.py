"""Фейковая платёжная система для UI.

Браузер не может подписать вебхук оплаты: секрет должен оставаться на сервере.
Поэтому UI «оплачивает» здесь, а этот сервис, как настоящая платёжка, присылает
в gen-service подписанный POST /webhooks/payments. Опция duplicates отправляет
один и тот же вебхук несколько раз одновременно — видно, что начисление одно.
"""

import asyncio
import json
import logging
from uuid import uuid4

import httpx
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from app.signing import sign

log = logging.getLogger("fake_payments")


class PaymentsSettings(BaseSettings):
    webhook_url: str = "http://api:8000/webhooks/payments"
    webhook_secret: str = "dev-secret"
    # Задержка «подтверждения банком» перед вебхуком.
    confirm_delay_seconds: float = 0.5


class CheckoutRequest(BaseModel):
    user_id: int
    tokens: int = Field(gt=0, le=10_000)
    duplicates: int = Field(1, ge=1, le=20)


class CheckoutResponse(BaseModel):
    payment_id: str


settings = PaymentsSettings()
_background: set[asyncio.Task[None]] = set()
app = FastAPI(title="fake-payments")
# Фейк вызывается прямо из браузера со страницы gen-service, это другой origin.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.post("/checkout", status_code=status.HTTP_202_ACCEPTED, response_model=CheckoutResponse)
async def checkout(request: CheckoutRequest) -> CheckoutResponse:
    payment_id = f"pay-{uuid4()}"
    body = json.dumps(
        {
            "payment_id": payment_id,
            "user_id": request.user_id,
            "tokens": request.tokens,
            "status": "succeeded",
        }
    ).encode()
    task = asyncio.create_task(_confirm(body, request.duplicates))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return CheckoutResponse(payment_id=payment_id)


async def _confirm(body: bytes, duplicates: int) -> None:
    await asyncio.sleep(settings.confirm_delay_seconds)
    headers = {"Content-Type": "application/json", "X-Signature": sign(body, settings.webhook_secret)}
    async with httpx.AsyncClient(timeout=10) as client:
        results = await asyncio.gather(
            *(client.post(settings.webhook_url, content=body, headers=headers) for _ in range(duplicates)),
            return_exceptions=True,
        )
    for result in results:
        if isinstance(result, Exception):
            log.warning("payment webhook failed: %r", result)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
