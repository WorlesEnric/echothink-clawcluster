from __future__ import annotations

from enum import Enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PublishTarget(str, Enum):
    OUTLINE = "outline"
    GITLAB_BRANCH = "gitlab_branch"
    GITLAB_MR = "gitlab_mr"
    DIFY = "dify"
    N8N = "n8n"


class PublishedArtifact(BaseModel):
    kind: str
    uri: str
    checksum: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublishRequest(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    work_item_id: str = Field(min_length=4)
    task_run_id: UUID
    target: PublishTarget
    artifact_uris: list[str] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_uris")
    @classmethod
    def validate_artifact_uris(cls, artifact_uris: list[str]) -> list[str]:
        for artifact_uri in artifact_uris:
            if not artifact_uri.startswith("s3://"):
                raise ValueError("artifact_uris must contain s3:// URIs")
        return artifact_uris


class PublishResult(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    work_item_id: str
    task_run_id: UUID
    target: PublishTarget
    success: bool
    status: Literal["published", "skipped", "failed"]
    idempotent: bool = False
    message: str | None = None
    external_refs: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[PublishedArtifact] = Field(default_factory=list)
    response_metadata: dict[str, Any] = Field(default_factory=dict)


class PublishStatus(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    work_item_id: str
    task_run_id: UUID
    work_item_status: str
    task_run_status: str
    external_refs: dict[str, Any] = Field(default_factory=dict)
    published_targets: list[PublishTarget] = Field(default_factory=list)
    artifacts: list[PublishedArtifact] = Field(default_factory=list)
