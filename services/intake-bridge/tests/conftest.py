from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.config import Settings
from src.main import create_app
from src.models.work_item import WorkItem, WorkItemCreate, WorkItemLifecycleStatus


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.inserted: list[WorkItem] = []

    async def insert_work_item(self, work_item: WorkItemCreate) -> WorkItem:
        now = datetime.now(timezone.utc)
        stored = WorkItem(
            **work_item.model_dump(),
            status=WorkItemLifecycleStatus.pending,
            created_at=now,
            updated_at=now,
        )
        self.inserted.append(stored)
        return stored

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class FakeMinioClient:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    async def stage_work_item_spec(self, work_item_id: str, markdown: str) -> str:
        key = f"hiclaw-storage/shared/tasks/task-{work_item_id}/spec.md"
        self.objects[key] = markdown
        return key

    async def ping(self) -> bool:
        return True


class FakeManagerNotifier:
    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []

    async def notify_work_item(self, work_item: WorkItem) -> dict[str, Any]:
        payload = work_item.model_dump(mode="json")
        self.notifications.append(payload)
        return {"accepted": True, "id": work_item.id}

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


@dataclass
class TestServices:
    supabase: FakeSupabaseClient
    minio: FakeMinioClient
    manager: FakeManagerNotifier


@pytest.fixture
def settings() -> Settings:
    return Settings(
        port=8100,
        outline_url="https://outline.example.com",
        outline_api_token="outline-token",
        gitlab_url="https://gitlab.example.com",
        gitlab_token="gitlab-token",
        supabase_url="postgresql://postgres:postgres@localhost:5432/postgres",
        supabase_service_key="service-role-secret",
        minio_endpoint="https://minio.example.com",
        minio_access_key="minio-access-key",
        minio_secret_key="minio-secret-key",
        minio_hiclaw_bucket="clawcluster-sharedfs",
        hiclaw_manager_port=8088,
        cluster_name="clawcluster-test",
        domain="example.internal",
        worker_jwt_secret="super-secret-webhook-key",
    )


@pytest.fixture
def sign_payload(settings: Settings):
    secret = settings.worker_jwt_secret.get_secret_value().encode("utf-8")

    def _sign(body: bytes) -> str:
        digest = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    return _sign


@pytest_asyncio.fixture
async def service_app(settings: Settings):
    services = TestServices(
        supabase=FakeSupabaseClient(),
        minio=FakeMinioClient(),
        manager=FakeManagerNotifier(),
    )
    app = create_app(
        settings=settings,
        supabase=services.supabase,
        minio=services.minio,
        manager=services.manager,
    )

    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def app_client(service_app):
    transport = ASGITransport(app=service_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
