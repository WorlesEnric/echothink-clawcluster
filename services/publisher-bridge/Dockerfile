FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app app \
    && mkdir -p /var/lib/clawcluster \
    && chown -R app:app /var/lib/clawcluster /app

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --upgrade pip \
    && pip install .

USER app

EXPOSE 8101

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8101}"]
