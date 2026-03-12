import logging
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import FastAPI, Request

from api.routes import router
from config import get_settings
from logging_utils import configure_logging
from policies.approval import ApprovalPolicy
from policies.budget import BudgetPolicy
from policies.evaluator import PolicyEvaluator
from storage.matrix import MatrixNotifier
from storage.supabase import SupabaseStorage


settings = get_settings()
configure_logging(service_name=settings.service_name)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = await SupabaseStorage.create(dsn=settings.supabase_dsn)
    notifier = MatrixNotifier(
        homeserver_url=settings.matrix_homeserver_url,
        access_token=settings.matrix_access_token.get_secret_value() if settings.matrix_access_token else None,
        server_name=settings.tuwunel_server_name,
    )
    evaluator = PolicyEvaluator(
        approval_policy=ApprovalPolicy(storage=storage, notifier=notifier),
        budget_policy=BudgetPolicy(storage=storage),
    )

    app.state.settings = settings
    app.state.supabase_storage = storage
    app.state.matrix_notifier = notifier
    app.state.policy_evaluator = evaluator
    logger.info("Policy bridge started", extra={"port": settings.port})
    try:
        yield
    finally:
        await notifier.close()
        await storage.close()
        logger.info("Policy bridge stopped")


app = FastAPI(title="policy-bridge", lifespan=lifespan)
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
