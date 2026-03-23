from __future__ import annotations

from typing import Any

import httpx

from src.config import Settings


class TaskCenterCallbackClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=settings.request_timeout_seconds)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def post_event(self, payload: dict[str, Any]) -> None:
        response = await self._client.post(
            self._settings.taskcenter_callback_url,
            json=payload,
            headers=self._settings.taskcenter_callback_headers,
        )
        response.raise_for_status()
