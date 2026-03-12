from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI, Request, Response

from api import router
from config import Settings, configure_logging, get_settings, request_id_context
from models.publish import PublishTarget
from publishers import DifyPublisher, GitLabPublisher, N8nPublisher, OutlinePublisher, PublisherRegistry
from storage import MinioArtifactStore, SupabaseRepository

try:
    import gitlab as gitlab_module
except ImportError:  # pragma: no cover - exercised only in minimal local environments
    gitlab_module = None

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    registry: PublisherRegistry
    supabase: SupabaseRepository
    artifact_store: MinioArtifactStore
    outline_client: httpx.AsyncClient
    dify_client: httpx.AsyncClient
    n8n_client: httpx.AsyncClient
    gitlab_client: object


def create_http_client(base_url: str, headers: dict[str, str]) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=30.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging()

    artifact_store = MinioArtifactStore(
        endpoint_url=settings.minio_endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        default_bucket=settings.minio_hiclaw_bucket,
    )
    supabase = SupabaseRepository(settings.supabase_url)
    await supabase.connect()

    outline_client = create_http_client(
        settings.outline_url,
        {"Authorization": f"Bearer {settings.outline_api_token.get_secret_value()}"},
    )
    dify_client = create_http_client(
        settings.dify_url,
        {"Authorization": f"Bearer {settings.dify_api_key.get_secret_value()}"},
    )
    n8n_client = create_http_client(
        settings.n8n_url,
        {"X-N8N-API-KEY": settings.n8n_api_key.get_secret_value()},
    )

    if gitlab_module is None:
        raise RuntimeError("python-gitlab is required to run publisher-bridge")
    gitlab_client = gitlab_module.Gitlab(
        url=settings.gitlab_url,
        private_token=settings.gitlab_token.get_secret_value(),
    )

    gitlab_publisher = GitLabPublisher(artifact_store=artifact_store, gitlab_client=gitlab_client)
    registry = PublisherRegistry(
        {
            PublishTarget.OUTLINE: OutlinePublisher(artifact_store=artifact_store, http_client=outline_client),
            PublishTarget.GITLAB_BRANCH: gitlab_publisher,
            PublishTarget.GITLAB_MR: gitlab_publisher,
            PublishTarget.DIFY: DifyPublisher(artifact_store=artifact_store, http_client=dify_client),
            PublishTarget.N8N: N8nPublisher(artifact_store=artifact_store, http_client=n8n_client),
        }
    )

    app.state.container = AppContainer(
        settings=settings,
        registry=registry,
        supabase=supabase,
        artifact_store=artifact_store,
        outline_client=outline_client,
        dify_client=dify_client,
        n8n_client=n8n_client,
        gitlab_client=gitlab_client,
    )
    logger.info("service.started", extra={"service": "publisher-bridge", "port": settings.port})
    try:
        yield
    finally:
        await outline_client.aclose()
        await dify_client.aclose()
        await n8n_client.aclose()
        await supabase.close()
        logger.info("service.stopped", extra={"service": "publisher-bridge"})


app = FastAPI(title="publisher-bridge", version="0.1.0", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next) -> Response:
    correlation_id = (
        request.headers.get("x-request-id")
        or request.headers.get("x-correlation-id")
        or str(uuid.uuid4())
    )
    start = time.perf_counter()
    token = request_id_context.set(correlation_id)
    request.state.correlation_id = correlation_id

    logging.getLogger("publisher_bridge.http").info(
        "request.started",
        extra={"method": request.method, "path": request.url.path},
    )
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        logging.getLogger("publisher_bridge.http").exception(
            "request.failed",
            extra={"method": request.method, "path": request.url.path, "duration_ms": duration_ms},
        )
        request_id_context.reset(token)
        raise

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Request-ID"] = correlation_id
    logging.getLogger("publisher_bridge.http").info(
        "request.completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    request_id_context.reset(token)
    return response


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run("main:app", host="0.0.0.0", port=settings.port, reload=False)
