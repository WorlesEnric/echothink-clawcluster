from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_gitlab_mr_webhook_creates_code_review_work_item(app_client, service_app, sign_payload) -> None:
    payload = {
        "object_kind": "merge_request",
        "event_type": "merge_request",
        "user": {"name": "Morgan Reviewer", "username": "mreviewer"},
        "project": {
            "id": 17,
            "path_with_namespace": "echothink/clawcluster",
            "web_url": "https://gitlab.example.com/echothink/clawcluster",
        },
        "object_attributes": {
            "id": 99,
            "iid": 7,
            "title": "Review intake bridge implementation",
            "description": """
## Objective
Review the intake bridge for production readiness.

## Acceptance Criteria
- Verify webhook signature handling.
- Confirm async storage and manager notification behavior.
- Check health reporting for database and manager dependencies.
""",
            "action": "open",
            "url": "https://gitlab.example.com/echothink/clawcluster/-/merge_requests/7",
            "source_branch": "feature/intake-bridge",
            "target_branch": "main",
        },
        "labels": [{"title": "review"}, {"title": "backend"}],
    }
    body = json.dumps(payload).encode("utf-8")

    response = await app_client.post(
        "/webhooks/gitlab",
        content=body,
        headers={
            "content-type": "application/json",
            "x-gitlab-signature-256": sign_payload(body),
        },
    )

    assert response.status_code == 201
    response_payload = response.json()
    work_item = response_payload["work_item"]

    assert work_item["source_type"] == "gitlab_mr"
    assert work_item["kind"] == "code.review"
    assert work_item["workspace_id"] == "echothink/clawcluster"
    assert work_item["requested_by"] == "mreviewer"
    assert work_item["acceptance_criteria"][0] == "Verify webhook signature handling."
    assert service_app.state.manager.notifications[0]["id"] == work_item["id"]


@pytest.mark.asyncio
async def test_gitlab_webhook_requires_supported_payload(app_client, sign_payload) -> None:
    payload = {"object_kind": "merge_request", "user": {"username": "bot"}, "project": {"id": 1}}
    body = json.dumps(payload).encode("utf-8")

    response = await app_client.post(
        "/webhooks/gitlab",
        content=body,
        headers={
            "content-type": "application/json",
            "x-gitlab-signature-256": sign_payload(body),
        },
    )

    assert response.status_code == 422
