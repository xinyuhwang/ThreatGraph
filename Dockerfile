FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so code changes don't invalidate the layer.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[dev]"

COPY . .

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
