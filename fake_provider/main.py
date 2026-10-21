"""Фейковый провайдер генерации. Один код на alpha и beta, имя задаётся PROVIDER_NAME.

POST /tasks сразу отвечает 202 {"external_id"}, а через delay_min..delay_max секунд
присылает подписанный вебхук completed или failed. Поведение меняется на лету через
PUT /control/behavior, чтобы тесты включали нужный сбой без перезапуска:

- failure_rate, failure_type      — доля отказов и тип ошибки в вебхуке;
- sync_failure_rate, sync_failure — 503 или зависание на сам POST /tasks;
- duplicate_webhook               — вебхук приходит дважды, подряд или одновременно;
- drop_webhook_rate               — вебхук не приходит совсем;
- delay_min > PROVIDER_TIMEOUT    — completed приходит после того, как сервис перестал ждать.

Поведение фиксируется в момент приёма задачи: смена настроек не влияет на задачи,
которые уже в работе. Состояние в памяти: фейк работает в одном процессе.
"""

import asyncio
import json
import logging
import random
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings

from app.signing import sign

log = logging.getLogger("fake_provider")


class ProviderSettings(BaseSettings):
    provider_name: str = "alpha"
    webhook_url: str = "http://api:8000/webhooks/providers/alpha"
    webhook_secret: str = "dev-secret"


class Behavior(BaseModel):
    delay_min: float = Field(2, ge=0)
    delay_max: float = Field(20, ge=0)
    failure_rate: float = Field(0, ge=0, le=1)
    failure_type: Literal["technical", "moderation", "payload"] = "technical"
    sync_failure_rate: float = Field(0, ge=0, le=1)
    sync_failure: Literal["503", "timeout"] = "503"
    # Сколько висит POST при sync_failure=timeout. Потом задача всё же принимается,
    # как у настоящего провайдера, до которого запрос дошёл, а ответ — нет.
    sync_timeout_seconds: float = Field(30, ge=0)
    drop_webhook_rate: float = Field(0, ge=0, le=1)
    duplicate_webhook: Literal["none", "sequential", "concurrent"] = "none"
    # Доставка с ретраями на не-2xx, как у настоящих провайдеров.
    webhook_retries: int = Field(5, ge=0)
    webhook_retry_delay: float = Field(1, ge=0)

    @model_validator(mode="after")
    def delays_ordered(self) -> "Behavior":
        if self.delay_max < self.delay_min:
            raise ValueError("delay_max must be >= delay_min")
        return self


class SubmitRequest(BaseModel):
    prompt: str
    ref_images: list[str] = Field(default_factory=list)


class SubmitResponse(BaseModel):
    external_id: str


settings = ProviderSettings()
behavior = Behavior()
_background: set[asyncio.Task[None]] = set()
app = FastAPI(title=f"fake-provider-{settings.provider_name}")


@app.post("/tasks", status_code=status.HTTP_202_ACCEPTED, response_model=SubmitResponse)
async def submit(payload: SubmitRequest):
    plan = behavior.model_copy()
    if random.random() < plan.sync_failure_rate:
        if plan.sync_failure == "503":
            return JSONResponse({"detail": "service unavailable"}, status.HTTP_503_SERVICE_UNAVAILABLE)
        await asyncio.sleep(plan.sync_timeout_seconds)

    external_id = f"{settings.provider_name}-{uuid4()}"
    task = asyncio.create_task(_run(external_id, plan))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return SubmitResponse(external_id=external_id)


async def _run(external_id: str, plan: Behavior) -> None:
    await asyncio.sleep(random.uniform(plan.delay_min, plan.delay_max))
    if random.random() < plan.drop_webhook_rate:
        log.info("%s: webhook dropped", external_id)
        return

    if random.random() < plan.failure_rate:
        payload = {"external_id": external_id, "status": "failed", "error_type": plan.failure_type}
    else:
        payload = {
            "external_id": external_id,
            "status": "completed",
            "result_url": f"https://files.example/{settings.provider_name}/{external_id}.png",
        }
    body = json.dumps(payload).encode()

    if plan.duplicate_webhook == "concurrent":
        await asyncio.gather(_deliver(body, plan), _deliver(body, plan))
    else:
        await _deliver(body, plan)
        if plan.duplicate_webhook == "sequential":
            await _deliver(body, plan)


async def _deliver(body: bytes, plan: Behavior) -> None:
    headers = {
        "Content-Type": "application/json",
        "X-Signature": sign(body, settings.webhook_secret),
    }
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(plan.webhook_retries + 1):
            try:
                response = await client.post(settings.webhook_url, content=body, headers=headers)
                if response.is_success:
                    return
                log.info("webhook got HTTP %s (try %s)", response.status_code, attempt + 1)
            except httpx.HTTPError as exc:
                log.info("webhook failed: %r (try %s)", exc, attempt + 1)
            await asyncio.sleep(plan.webhook_retry_delay)


@app.get("/control/behavior", response_model=Behavior)
async def get_behavior() -> Behavior:
    return behavior


@app.put("/control/behavior", response_model=Behavior)
async def set_behavior(new: Behavior) -> Behavior:
    global behavior
    behavior = new
    return behavior


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": settings.provider_name}
