import httpx
import pytest

from linkers.langfuse import LangfuseLinker


@pytest.mark.anyio
async def test_fetch_trace_metrics_parses_nested_usage_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/public/traces/trace-123"
        assert request.headers["Authorization"] == "Bearer secret-key"
        return httpx.Response(
            status_code=200,
            json={
                "data": {
                    "id": "trace-123",
                    "usage": {"totalCost": 1.75, "totalTokens": 3210},
                }
            },
        )

    linker = LangfuseLinker(
        base_url="https://langfuse.example.com",
        secret_key="secret-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    metrics = await linker.fetch_trace_metrics("trace-123")

    assert metrics.trace_id == "trace-123"
    assert metrics.cost_usd == pytest.approx(1.75)
    assert metrics.token_count == 3210


@pytest.mark.anyio
async def test_fetch_trace_metrics_falls_back_to_recursive_keys():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=200,
            json={
                "data": {
                    "observations": [
                        {"name": "generation", "metrics": {"costUsd": 2.5, "tokenCount": 4096}}
                    ]
                }
            },
        )

    linker = LangfuseLinker(
        base_url="https://langfuse.example.com",
        secret_key="secret-key",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    metrics = await linker.fetch_trace_metrics("trace-123")

    assert metrics.cost_usd == pytest.approx(2.5)
    assert metrics.token_count == 4096
