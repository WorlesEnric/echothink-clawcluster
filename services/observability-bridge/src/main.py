import logging
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request

from api.routes import router
from config import get_settings
from linkers.graphiti import GraphitiClient
from linkers.langfuse import LangfuseLinker
from linkers.supabase import SupabaseTaskRunStore
from logging_utils import configure_logging


settings = get_settings()
configure_logging(service_name=settings.service_name)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = await SupabaseTaskRunStore.create(dsn=settings.supabase_dsn)
    langfuse_linker = LangfuseLinker(
        base_url=settings.langfuse_url,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
    )
    graphiti_client = GraphitiClient(base_url=settings.graphiti_url)

    app.state.settings = settings
    app.state.task_run_store = store
    app.state.langfuse_linker = langfuse_linker
    app.state.graphiti_client = graphiti_client
    logger.info("Observability bridge started", extra={"port": settings.port})
    try:
        yield
    finally:
        await graphiti_client.close()
        await langfuse_linker.close()
        await store.close()
        logger.info("Observability bridge stopped")


app = FastAPI(title="observability-bridge", lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = perf_counter()
    response = await call_next(request)
    duration_ms = round((perf_counter() - start) * 1000, 3)
    logger.info(
        "HTTP request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response
