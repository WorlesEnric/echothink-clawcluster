from __future__ import annotations

from typing import Any

import httpx

from src.config import Settings


class IntakeBridgeClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.intake_bridge_base_url,
            timeout=settings.request_timeout_seconds,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ping(self) -> bool:
        response = await self._client.get("/health")
        return response.status_code < 500

    async def create_work_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("/work-items", json=payload)
        response.raise_for_status()
        return dict(response.json() or {})
