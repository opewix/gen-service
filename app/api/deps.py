from fastapi import Header, HTTPException, Request, status

from app.config import get_settings
from app.signing import verify


async def signed_body(request: Request, x_signature: str | None = Header(default=None)) -> bytes:
    """Сырое тело запроса с проверенной подписью X-Signature.

    Подпись считается от сырых байтов, поэтому тело читаем сами и валидируем
    pydantic-моделью уже после проверки, а не через автопарсинг FastAPI.
    """
    body = await request.body()
    if not verify(body, x_signature, get_settings().webhook_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid signature")
    return body
