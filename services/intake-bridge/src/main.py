from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from src.api.routes import router
from src.config import Settings, get_settings
from src.handlers.gitlab import GitLabHandler
from src.handlers.outline import OutlineHandler
from src.notify.manager import ManagerNotifier
from src.storage.minio import MinioClient
from src.storage.supabase import SupabaseClient

APP_VERSION = "1.0.0"
_LOGGING_CONFIGURED = False
_RESERVED_LOG_FIELDS = {
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
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_FIELDS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging() -> None:
    global _LOGGING_CONFIGURED

    if _LOGGING_CONFIGURED:
        return

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    _LOGGING_CONFIGURED = True


def create_app(
    *,
    settings: Settings | None = None,
    supabase: SupabaseClient | None = None,
    minio: MinioClient | None = None,
    manager: ManagerNotifier | None = None,
    outline_handler: OutlineHandler | None = None,
    gitlab_handler: GitLabHandler | None = None,
) -> FastAPI:
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        resolved_settings = settings or get_settings()

        app.state.settings = resolved_settings
        app.state.supabase = supabase or SupabaseClient(resolved_settings)
        app.state.minio = minio or MinioClient(resolved_settings)
        app.state.manager = manager or ManagerNotifier(resolved_settings)
        app.state.outline_handler = outline_handler or OutlineHandler(resolved_settings)
        app.state.gitlab_handler = gitlab_handler or GitLabHandler(resolved_settings)

        logger.info(
            "application_started",
            extra={
                "service": "intake-bridge",
                "version": APP_VERSION,
                "port": resolved_settings.port,
                "cluster_name": resolved_settings.cluster_name,
            },
        )

        try:
            yield
        finally:
            await app.state.manager.close()
            await app.state.supabase.close()
            logger.info("application_stopped", extra={"service": "intake-bridge"})

    app = FastAPI(title="intake-bridge", version=APP_VERSION, lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
