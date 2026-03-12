from __future__ import annotations

from typing import Any

import httpx

from models.publish import PublishedArtifact, PublishRequest, PublishResult
from publishers.base import ArtifactStore, BasePublisher


class OutlinePublisher(BasePublisher):
    def __init__(self, artifact_store: ArtifactStore, http_client: httpx.AsyncClient) -> None:
        super().__init__(artifact_store)
        self.http_client = http_client

    async def publish(self, request: PublishRequest) -> PublishResult:
        artifact_uri = request.metadata.get("artifact_uri") or self.select_artifact_uri(
            request.artifact_uris,
            (".md", ".markdown"),
        )
        markdown = await self.artifact_store.get_text(artifact_uri)
        title = str(request.metadata.get("title") or self.default_label(artifact_uri))
        document_id = request.metadata.get("document_id")

        payload: dict[str, Any] = {
            "title": title,
            "text": markdown,
            "publish": bool(request.metadata.get("publish", True)),
        }
        if document_id:
            payload["id"] = document_id
            endpoint_path = "/api/documents.update"
        else:
            endpoint_path = "/api/documents.create"
            if collection_id := request.metadata.get("collection_id"):
                payload["collectionId"] = collection_id
            if parent_document_id := request.metadata.get("parent_document_id"):
                payload["parentDocumentId"] = parent_document_id

        response = await self.http_client.post(endpoint_path, json=payload)
        response.raise_for_status()
        body = response.json()
        document = body.get("data", body)
        published_document_id = str(document.get("id") or document_id or "")
        if not published_document_id:
            raise ValueError("Outline response did not include a document id")

        return PublishResult(
            work_item_id=request.work_item_id,
            task_run_id=request.task_run_id,
            target=request.target,
            success=True,
            status="published",
            message="Outline document published successfully.",
            external_refs={"outline_doc_id": published_document_id},
            artifacts=[
                PublishedArtifact(
                    kind="outline_draft",
                    uri=f"outline://documents/{published_document_id}",
                    metadata={
                        "operation": "update" if document_id else "create",
                        "source_artifact_uri": artifact_uri,
                        "title": title,
                    },
                )
            ],
            response_metadata={
                "document_id": published_document_id,
                "title": title,
                "url": document.get("url"),
            },
        )
