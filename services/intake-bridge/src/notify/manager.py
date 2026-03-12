from __future__ import annotations

from typing import Any

import httpx

from src.config import Settings
from src.models.work_item import WorkItem


class ManagerNotifier:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.manager_base_url,
            timeout=settings.request_timeout_seconds,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ping(self) -> bool:
        response = await self._client.get("/health")
        return response.status_code < 500

    async def notify_work_item(self, work_item: WorkItem) -> dict[str, Any]:
        response = await self._client.post(
            f"/work-items/{work_item.id}",
            json=work_item.model_dump(mode="json"),
        )
        response.raise_for_status()
        if response.content:
            try:
                return response.json()
            except ValueError:
                return {"status_code": response.status_code, "body": response.text}
        return {"status_code": response.status_code}
