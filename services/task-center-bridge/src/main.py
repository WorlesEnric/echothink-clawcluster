from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI

from src.api import router
from src.clients.intake_bridge import IntakeBridgeClient
from src.clients.taskcenter import TaskCenterCallbackClient
from src.config import Settings, get_settings
from src.services.bridge import TaskCenterBridgeService
from src.storage.repository import TaskCenterBridgeRepository

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


async def _run_periodic(
    *,
    name: str,
    interval_seconds: float,
    callback,
) -> None:
    logger = logging.getLogger(__name__)
    while True:
        try:
            await callback()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("background_loop_failed", extra={"loop": name})
        await asyncio.sleep(interval_seconds)


def create_app(
    *,
    settings: Settings | None = None,
    repository: TaskCenterBridgeRepository | None = None,
    intake_bridge: IntakeBridgeClient | None = None,
    callback_client: TaskCenterCallbackClient | None = None,
) -> FastAPI:
    logger = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        resolved_settings = settings or get_settings()

        resolved_repository = repository or TaskCenterBridgeRepository(resolved_settings.supabase_dsn)
        await resolved_repository.connect()
        await resolved_repository.ensure_schema()

        resolved_intake_bridge = intake_bridge or IntakeBridgeClient(resolved_settings)
        resolved_callback_client = callback_client or TaskCenterCallbackClient(resolved_settings)
        bridge_service = TaskCenterBridgeService(
            settings=resolved_settings,
            repository=resolved_repository,
            intake_bridge=resolved_intake_bridge,
            callback_client=resolved_callback_client,
        )

        app.state.settings = resolved_settings
        app.state.repository = resolved_repository
        app.state.intake_bridge = resolved_intake_bridge
        app.state.callback_client = resolved_callback_client
        app.state.bridge_service = bridge_service

        app.state.reconcile_task = asyncio.create_task(
            _run_periodic(
                name="reconcile",
                interval_seconds=resolved_settings.reconcile_interval_seconds,
                callback=bridge_service.reconcile_once,
            )
        )
        app.state.outbox_task = asyncio.create_task(
            _run_periodic(
                name="outbox",
                interval_seconds=resolved_settings.outbox_interval_seconds,
                callback=bridge_service.deliver_outbox_once,
            )
        )

        logger.info(
            "application_started",
            extra={
                "service": "task-center-bridge",
                "version": APP_VERSION,
                "port": resolved_settings.port,
                "cluster_name": resolved_settings.cluster_name,
            },
        )

        try:
            yield
        finally:
            for task in (app.state.reconcile_task, app.state.outbox_task):
                task.cancel()
            await asyncio.gather(app.state.reconcile_task, app.state.outbox_task, return_exceptions=True)
            await resolved_callback_client.close()
            await resolved_intake_bridge.close()
            await resolved_repository.close()
            logger.info("application_stopped", extra={"service": "task-center-bridge"})

    app = FastAPI(title="task-center-bridge", version=APP_VERSION, lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()
