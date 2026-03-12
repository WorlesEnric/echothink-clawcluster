FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN addgroup --system clawcluster \
    && adduser --system --ingroup clawcluster --home /app clawcluster

COPY pyproject.toml ./
COPY src ./src

RUN pip install --upgrade pip \
    && pip install .

USER clawcluster

EXPOSE 8103

CMD ["uvicorn", "main:app", "--app-dir", "/app/src", "--host", "0.0.0.0", "--port", "8103"]
