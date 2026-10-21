"""Фейковый провайдер генерации. Один код на alpha и beta, имя задаётся PROVIDER_NAME.

Пока заглушка: принимает задачу и отвечает 202. Вебхуки и настраиваемые сбои
(доля отказов, 503/таймаут, дубли, пропажа, поздний completed) будут добавлены отдельно.
"""

from uuid import uuid4

from fastapi import FastAPI, status
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class ProviderSettings(BaseSettings):
    provider_name: str = "alpha"
    webhook_url: str = "http://api:8000/webhooks/providers/alpha"
    webhook_secret: str = "dev-secret"


class SubmitRequest(BaseModel):
    prompt: str
    ref_images: list[str] = Field(default_factory=list)


class SubmitResponse(BaseModel):
    external_id: str


settings = ProviderSettings()
app = FastAPI(title=f"fake-provider-{settings.provider_name}")


@app.post("/tasks", status_code=status.HTTP_202_ACCEPTED, response_model=SubmitResponse)
async def submit(payload: SubmitRequest) -> SubmitResponse:
    return SubmitResponse(external_id=f"{settings.provider_name}-{uuid4()}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": settings.provider_name}
