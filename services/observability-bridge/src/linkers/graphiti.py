import httpx

from models.events import TaskCompleteEvent


class GraphitiClient:
    def __init__(self, base_url: str | None, client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    @property
    def enabled(self) -> bool:
        return bool(self._base_url)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def sync_task(self, event: TaskCompleteEvent) -> None:
        if not self._base_url:
            return

        response = await self._client.post(
            f"{self._base_url}/sync",
            json={
                "task_run_id": str(event.task_run_id),
                "work_item_id": event.work_item_id,
                "status": event.status.value,
                "trace_id": event.trace_id,
                "result_summary": event.result_summary,
                "metadata": event.metadata,
            },
        )
        response.raise_for_status()
