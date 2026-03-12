from __future__ import annotations

from src.config import Settings
from src.handlers.base import BaseHandler, PayloadNormalizationError
from src.models.webhooks import GitLabWebhookPayload
from src.models.work_item import SourceType, WorkItemCreate


class GitLabHandler(BaseHandler[GitLabWebhookPayload]):
    def __init__(self, settings: Settings) -> None:
        super().__init__(cluster_name=settings.cluster_name, domain=settings.domain)

    async def to_work_item(self, payload: GitLabWebhookPayload) -> WorkItemCreate:
        source_type = (
            SourceType.gitlab_mr if payload.object_kind == "merge_request" else SourceType.gitlab_issue
        )

        title = payload.object_attributes.title.strip()
        body = self.normalize_text(payload.object_attributes.description)
        if not title:
            raise PayloadNormalizationError("GitLab issue or merge request title is required")

        labels = payload.label_titles
        kind = self.classify_kind(title, body, source_type=source_type.value, labels=labels)
        risk_level = self.determine_risk(title, body, labels=labels)

        constraints_json = {
            "cluster_name": self.cluster_name,
            "domain": self.domain,
            "ingest_provider": "gitlab",
            "object_kind": payload.object_kind,
            "action": payload.object_attributes.action,
            "project_id": payload.project.id,
            "project_path": payload.project.path_with_namespace,
            "project_url": payload.project.web_url,
            "source_id": payload.object_attributes.id,
            "source_iid": payload.object_attributes.iid,
            "source_title": payload.object_attributes.title,
            "source_url": payload.object_attributes.url,
            "source_content": payload.object_attributes.description or "",
            "labels": labels,
        }

        if payload.object_kind == "merge_request":
            constraints_json["source_branch"] = payload.object_attributes.source_branch
            constraints_json["target_branch"] = payload.object_attributes.target_branch

        return WorkItemCreate(
            workspace_id=payload.project.path_with_namespace or str(payload.project.id),
            kind=kind,
            source_type=source_type,
            source_ref=payload.object_attributes.url
            or f"gitlab:{payload.object_kind}:{payload.project.id}:{payload.object_attributes.iid}",
            objective=self.extract_objective(title, body),
            acceptance_criteria=self.extract_acceptance_criteria(body),
            constraints_json=constraints_json,
            priority=self.determine_priority(
                title,
                body,
                labels=labels,
                default=70 if source_type is SourceType.gitlab_mr else 60,
            ),
            risk_level=risk_level,
            approval_policy=self.determine_approval_policy(risk_level, kind),
            requested_by=self.build_requested_by(payload.user.display_name, "gitlab-webhook"),
        )
