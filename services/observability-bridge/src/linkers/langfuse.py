from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from models.events import TraceMetrics


class LangfuseLinker:
    def __init__(
        self,
        base_url: str,
        secret_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret_key = secret_key
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_trace_metrics(self, trace_id: str) -> TraceMetrics:
        response = await self._client.get(
            f"{self._base_url}/api/public/traces/{quote(trace_id, safe='')}",
            headers={"Authorization": f"Bearer {self._secret_key}"},
        )
        response.raise_for_status()

        payload = response.json()
        data = payload.get("data", payload)
        return TraceMetrics(
            trace_id=trace_id,
            cost_usd=self._extract_cost(data),
            token_count=self._extract_tokens(data),
            raw=data if isinstance(data, dict) else {"payload": data},
        )

    def _extract_cost(self, payload: Any) -> float | None:
        value = self._extract_first(
            payload,
            paths=[
                ("totalCost",),
                ("costUsd",),
                ("cost_usd",),
                ("metrics", "costUsd"),
                ("usage", "totalCost"),
                ("usageDetails", "totalCost"),
            ],
            recursive_keys={"totalCost", "costUsd", "cost_usd", "cost"},
        )
        return float(value) if value is not None else None

    def _extract_tokens(self, payload: Any) -> int | None:
        value = self._extract_first(
            payload,
            paths=[
                ("totalTokens",),
                ("tokenCount",),
                ("token_count",),
                ("metrics", "tokenCount"),
                ("usage", "totalTokens"),
                ("usageDetails", "totalTokens"),
            ],
            recursive_keys={"totalTokens", "tokenCount", "token_count", "tokens"},
        )
        return int(value) if value is not None else None

    def _extract_first(
        self,
        payload: Any,
        *,
        paths: list[tuple[str, ...]],
        recursive_keys: set[str],
    ) -> float | int | None:
        for path in paths:
            value = self._get_path(payload, path)
            if isinstance(value, (int, float)):
                return value

        for candidate in self._walk_mappings(payload):
            for key, value in candidate.items():
                if key in recursive_keys and isinstance(value, (int, float)):
                    return value

        return None

    def _get_path(self, payload: Any, path: Iterable[str]) -> Any:
        current = payload
        for key in path:
            if not isinstance(current, Mapping) or key not in current:
                return None
            current = current[key]
        return current

    def _walk_mappings(self, payload: Any) -> Iterable[Mapping[str, Any]]:
        if isinstance(payload, Mapping):
            yield payload
            for value in payload.values():
                yield from self._walk_mappings(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from self._walk_mappings(item)
