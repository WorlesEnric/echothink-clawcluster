from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.work_item import WorkItemCreate


def test_work_item_create_normalizes_fields() -> None:
    work_item = WorkItemCreate(
        workspace_id="  team-alpha  ",
        kind="code.implement",
        objective="  Ship the intake bridge MVP  ",
        acceptance_criteria=["  Handle webhook signatures  ", "Handle webhook signatures", "  "],
        constraints_json={"source_title": "Build intake bridge", "source_content": "Draft spec", "unused": None},
        requested_by="  alice@example.com  ",
    )

    assert work_item.id.startswith("wi_")
    assert work_item.workspace_id == "team-alpha"
    assert work_item.objective == "Ship the intake bridge MVP"
    assert work_item.acceptance_criteria == ["Handle webhook signatures"]
    assert "unused" not in work_item.constraints_json
    assert "## Source Content" in work_item.render_spec_markdown()


def test_work_item_priority_is_validated() -> None:
    with pytest.raises(ValidationError):
        WorkItemCreate(
            workspace_id="team-alpha",
            kind="code.implement",
            objective="Ship it",
            priority=101,
            requested_by="alice@example.com",
        )


@pytest.mark.asyncio
async def test_health_endpoint_reports_dependencies(app_client) -> None:
    response = await app_client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "1.0.0"
    assert payload["dependencies"]["database"]["status"] == "ok"
    assert payload["dependencies"]["manager"]["status"] == "ok"
