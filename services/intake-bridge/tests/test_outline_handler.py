from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_outline_webhook_creates_and_fanouts_work_item(app_client, service_app, sign_payload) -> None:
    payload = {
        "event": "documents.create",
        "data": {
            "id": "doc_123",
            "workspaceId": "outline-ws-1",
            "title": "Workflow: Publish approved drafts",
            "url": "https://outline.example.com/doc/doc_123",
            "text": """
## Objective
Build an n8n workflow to publish approved drafts automatically.

## Acceptance Criteria
- Trigger on approved Outline drafts.
- Publish the approved document into the target collection.
- Emit an audit trail entry after publication.
""",
        },
        "actor": {"name": "Casey Integrations", "email": "casey@example.com"},
    }
    body = json.dumps(payload).encode("utf-8")

    response = await app_client.post(
        "/webhooks/outline",
        content=body,
        headers={
            "content-type": "application/json",
            "x-outline-signature-256": sign_payload(body),
        },
    )

    assert response.status_code == 201
    payload = response.json()
    work_item = payload["work_item"]

    assert work_item["source_type"] == "outline_document"
    assert work_item["kind"] == "workflow.author"
    assert work_item["workspace_id"] == "outline-ws-1"
    assert work_item["objective"] == "Build an n8n workflow to publish approved drafts automatically."
    assert payload["processing"]["storage"]["status"] == "ok"
    assert payload["processing"]["manager"]["status"] == "ok"

    storage_key = f"shared/tasks/task-{work_item['id']}/spec.md"
    assert storage_key in service_app.state.minio.objects
    assert service_app.state.manager.notifications[0]["id"] == work_item["id"]
    assert "## Objective" in service_app.state.manager.notifications[0]["spec_markdown"]


@pytest.mark.asyncio
async def test_outline_webhook_rejects_invalid_signature(app_client) -> None:
    body = json.dumps(
        {
            "event": "documents.update",
            "data": {"id": "doc_456", "workspaceId": "outline-ws-1", "title": "Docs", "text": "content"},
        }
    ).encode("utf-8")

    response = await app_client.post(
        "/webhooks/outline",
        content=body,
        headers={"content-type": "application/json", "x-outline-signature-256": "sha256=bad"},
    )

    assert response.status_code == 400
