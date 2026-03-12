from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OutlineActor(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    name: str | None = None
    email: str | None = None

    @property
    def display_name(self) -> str | None:
        return self.name or self.email or self.id


class OutlineDocument(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    title: str
    text: str = ""
    url: str | None = None
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    team_id: str | None = Field(default=None, alias="teamId")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class OutlineWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: str
    data: OutlineDocument
    actor: OutlineActor | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        raw_data = payload.get("data") or payload.get("document") or {}
        if isinstance(raw_data, dict) and isinstance(raw_data.get("document"), dict):
            raw_data = raw_data["document"]

        actor = payload.get("actor") or payload.get("user")
        if actor is None and isinstance(raw_data, dict):
            actor = raw_data.get("createdBy") or raw_data.get("updatedBy")

        payload["data"] = raw_data
        payload["actor"] = actor
        payload["event"] = payload.get("event") or payload.get("name") or "documents.update"
        return payload

    @property
    def workspace_id(self) -> str | None:
        return self.data.workspace_id or self.data.team_id


class GitLabUser(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | str | None = None
    name: str | None = None
    username: str | None = None
    email: str | None = None

    @property
    def display_name(self) -> str | None:
        return self.username or self.name or self.email or (str(self.id) if self.id is not None else None)


class GitLabProject(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | str
    name: str | None = None
    path_with_namespace: str | None = None
    web_url: str | None = None


class GitLabLabel(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    color: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_label(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"title": value}
        if isinstance(value, dict) and "title" not in value and "name" in value:
            return {**value, "title": value["name"]}
        return value


class GitLabObjectAttributes(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int | str
    iid: int
    title: str
    description: str = ""
    action: str | None = None
    url: str | None = None
    source_branch: str | None = None
    target_branch: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GitLabWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    object_kind: Literal["issue", "merge_request"]
    event_type: str | None = None
    user: GitLabUser
    project: GitLabProject
    object_attributes: GitLabObjectAttributes
    labels: list[GitLabLabel] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _normalize_payload(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value

        payload = dict(value)
        labels = payload.get("labels") or []
        object_attributes = payload.get("object_attributes") or {}
        if not labels and isinstance(object_attributes, dict):
            labels = object_attributes.get("labels") or []

        payload["labels"] = labels
        payload["event_type"] = payload.get("event_type") or payload.get("object_kind")
        return payload

    @property
    def label_titles(self) -> list[str]:
        return [label.title for label in self.labels]
