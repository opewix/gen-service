from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://gen:gen@localhost:5432/gen"
    # Общий секрет для подписи вебхуков провайдеров и платёжки (X-Signature).
    webhook_secret: str = "dev-secret"
    # Сколько ждём вебхук от провайдера, прежде чем сторож признает попытку неудачной.
    provider_timeout_seconds: float = 60
    # Таймаут на сам POST /tasks к провайдеру.
    provider_request_timeout_seconds: float = 5
    max_active_tasks_per_user: int = 3
    # JSON в env: PROVIDER_URLS='{"alpha": "http://alpha:8000", "beta": "http://beta:8000"}'
    provider_urls: dict[str, str] = {
        "alpha": "http://alpha:8000",
        "beta": "http://beta:8000",
    }


@lru_cache
def get_settings() -> Settings:
    return Settings()
