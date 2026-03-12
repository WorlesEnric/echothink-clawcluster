from __future__ import annotations

import json

import httpx
import pytest

from models.publish import PublishTarget
from publishers.outline import OutlinePublisher


@pytest.mark.asyncio
async def test_outline_publisher_creates_document(artifact_store_factory, make_publish_request, mocker) -> None:
    artifact_uri = "s3://hiclaw-storage/shared/tasks/task-1/result.md"
    artifact_store = artifact_store_factory({artifact_uri: "# Approved Draft\n\nBody"})
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"data": {"id": "doc_123", "url": "https://outline/doc_123"}})

    client = httpx.AsyncClient(
        base_url="https://outline.example",
        transport=httpx.MockTransport(handler),
    )
    publisher = OutlinePublisher(artifact_store=artifact_store, http_client=client)
    get_text_spy = mocker.spy(artifact_store, "get_text")

    publish_request = make_publish_request(
        target=PublishTarget.OUTLINE,
        artifact_uris=[artifact_uri],
        metadata={"title": "Approved Draft"},
    )
    result = await publisher.publish(publish_request)

    assert captured["path"] == "/api/documents.create"
    assert captured["body"] == {
        "title": "Approved Draft",
        "text": "# Approved Draft\n\nBody",
        "publish": True,
    }
    assert get_text_spy.call_count == 1
    assert result.success is True
    assert result.external_refs == {"outline_doc_id": "doc_123"}
    assert result.artifacts[0].uri == "outline://documents/doc_123"

    await client.aclose()


@pytest.mark.asyncio
async def test_outline_publisher_updates_existing_document(artifact_store_factory, make_publish_request) -> None:
    artifact_uri = "s3://hiclaw-storage/shared/tasks/task-2/result.md"
    artifact_store = artifact_store_factory({artifact_uri: "# Revised Draft\n\nUpdated"})
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"data": {"id": "doc_456", "url": "https://outline/doc_456"}})

    client = httpx.AsyncClient(
        base_url="https://outline.example",
        transport=httpx.MockTransport(handler),
    )
    publisher = OutlinePublisher(artifact_store=artifact_store, http_client=client)

    publish_request = make_publish_request(
        target=PublishTarget.OUTLINE,
        artifact_uris=[artifact_uri],
        metadata={"title": "Revised Draft", "document_id": "doc_456", "publish": False},
    )
    result = await publisher.publish(publish_request)

    assert captured["path"] == "/api/documents.update"
    assert captured["body"] == {
        "title": "Revised Draft",
        "text": "# Revised Draft\n\nUpdated",
        "publish": False,
        "id": "doc_456",
    }
    assert result.external_refs == {"outline_doc_id": "doc_456"}

    await client.aclose()
