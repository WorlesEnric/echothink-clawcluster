from __future__ import annotations

from src.config import Settings
from src.handlers.base import BaseHandler, PayloadNormalizationError
from src.models.webhooks import OutlineWebhookPayload
from src.models.work_item import SourceType, WorkItemCreate


class OutlineHandler(BaseHandler[OutlineWebhookPayload]):
    def __init__(self, settings: Settings) -> None:
        super().__init__(cluster_name=settings.cluster_name, domain=settings.domain)

    async def to_work_item(self, payload: OutlineWebhookPayload) -> WorkItemCreate:
        if not payload.event.startswith("documents."):
            raise PayloadNormalizationError(f"Unsupported Outline event: {payload.event}")

        title = payload.data.title.strip()
        body = self.normalize_text(payload.data.text)
        if not title:
            raise PayloadNormalizationError("Outline document title is required")

        kind = self.classify_kind(title, body, source_type=SourceType.outline_document.value)
        risk_level = self.determine_risk(title, body)

        return WorkItemCreate(
            workspace_id=payload.workspace_id or self.cluster_name,
            kind=kind,
            source_type=SourceType.outline_document,
            source_ref=payload.data.url or f"outline:{payload.data.id}",
            objective=self.extract_objective(title, body),
            acceptance_criteria=self.extract_acceptance_criteria(body),
            constraints_json={
                "cluster_name": self.cluster_name,
                "domain": self.domain,
                "ingest_provider": "outline",
                "source_event": payload.event,
                "source_id": payload.data.id,
                "source_title": payload.data.title,
                "source_url": payload.data.url,
                "source_content": payload.data.text or "",
            },
            priority=self.determine_priority(title, body, default=45),
            risk_level=risk_level,
            approval_policy=self.determine_approval_policy(risk_level, kind),
            requested_by=self.build_requested_by(
                payload.actor.display_name if payload.actor else None,
                "outline-webhook",
            ),
        )
