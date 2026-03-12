from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

request_id_context: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", request_id_context.get()),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOG_RECORD_KEYS and not key.startswith("_")
        }
        if extras:
            payload["context"] = extras
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = request_id_context.get()
        return True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    port: int = Field(default=8101, alias="PORT", ge=1, le=65535)
    outline_url: str = Field(alias="OUTLINE_URL")
    outline_api_token: SecretStr = Field(alias="OUTLINE_API_TOKEN")
    gitlab_url: str = Field(alias="GITLAB_URL")
    gitlab_token: SecretStr = Field(alias="GITLAB_TOKEN")
    dify_url: str = Field(alias="DIFY_URL")
    dify_api_key: SecretStr = Field(alias="DIFY_API_KEY")
    n8n_url: str = Field(alias="N8N_URL")
    n8n_api_key: SecretStr = Field(alias="N8N_API_KEY")
    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_service_key: SecretStr = Field(alias="SUPABASE_SERVICE_KEY")
    minio_endpoint: str = Field(alias="MINIO_ENDPOINT")
    minio_access_key: SecretStr = Field(alias="MINIO_ACCESS_KEY")
    minio_secret_key: SecretStr = Field(alias="MINIO_SECRET_KEY")
    minio_hiclaw_bucket: str = Field(alias="MINIO_HICLAW_BUCKET")
    worker_jwt_secret: SecretStr = Field(alias="WORKER_JWT_SECRET")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def configure_logging(level: int = logging.INFO) -> None:
    root_logger = logging.getLogger()
    if getattr(root_logger, "_publisher_bridge_configured", False):
        root_logger.setLevel(level)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(CorrelationIdFilter())

    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    root_logger._publisher_bridge_configured = True  # type: ignore[attr-defined]
