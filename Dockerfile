FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Сначала зависимости — слой кэшируется, пока не меняется pyproject.toml.
COPY pyproject.toml ./
RUN mkdir app fake_provider && touch app/__init__.py fake_provider/__init__.py \
    && pip install ".[dev]" && pip uninstall -y gen-service

COPY . .
RUN pip install --no-deps . && useradd --create-home appuser
USER appuser

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
