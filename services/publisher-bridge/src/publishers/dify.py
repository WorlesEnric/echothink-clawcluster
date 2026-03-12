from __future__ import annotations

from typing import Any

import httpx

from models.publish import PublishedArtifact, PublishRequest, PublishResult
from publishers.base import ArtifactStore, BasePublisher


class DifyPublisher(BasePublisher):
    def __init__(self, artifact_store: ArtifactStore, http_client: httpx.AsyncClient) -> None:
        super().__init__(artifact_store)
        self.http_client = http_client

    async def publish(self, request: PublishRequest) -> PublishResult:
        artifact_uri = request.metadata.get("artifact_uri") or self.select_artifact_uri(request.artifact_uris, (".json",))
        workflow_definition = await self.artifact_store.get_json(artifact_uri)
        endpoint_path = str(request.metadata.get("endpoint_path", "/v1/workflows/import"))
        body = self._build_request_body(request.metadata, workflow_definition)
        method = str(request.metadata.get("http_method", "POST")).upper()

        response = await self.http_client.request(method, endpoint_path, json=body)
        response.raise_for_status()
        payload = response.json()
        workflow_id = str(self._extract_identifier(payload, request.metadata.get("workflow_id_path")))
        if not workflow_id:
            raise ValueError("Dify response did not include a workflow identifier")

        return PublishResult(
            work_item_id=request.work_item_id,
            task_run_id=request.task_run_id,
            target=request.target,
            success=True,
            status="published",
            message="Dify workflow published successfully.",
            external_refs={"dify_workflow_id": workflow_id},
            artifacts=[
                PublishedArtifact(
                    kind="workflow_draft",
                    uri=f"dify://workflows/{workflow_id}",
                    metadata={"source_artifact_uri": artifact_uri},
                )
            ],
            response_metadata={"workflow_id": workflow_id, "endpoint_path": endpoint_path},
        )

    def _build_request_body(self, metadata: dict[str, Any], workflow_definition: Any) -> Any:
        request_body = metadata.get("request_body")
        if request_body is None:
            return workflow_definition
        if isinstance(request_body, dict) and metadata.get("embed_artifact", True):
            return {**request_body, "workflow": workflow_definition}
        return request_body

    def _extract_identifier(self, payload: Any, explicit_path: Any) -> Any:
        if explicit_path:
            current = payload
            for part in str(explicit_path).split("."):
                if isinstance(current, dict):
                    current = current.get(part)
                else:
                    current = None
                if current is None:
                    break
            return current

        candidates = [
            payload.get("id") if isinstance(payload, dict) else None,
            payload.get("workflow_id") if isinstance(payload, dict) else None,
            payload.get("data", {}).get("id") if isinstance(payload, dict) else None,
            payload.get("workflow", {}).get("id") if isinstance(payload, dict) else None,
        ]
        return next((candidate for candidate in candidates if candidate not in (None, "")), None)
