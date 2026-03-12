from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import Any, Protocol, Sequence

from models.publish import PublishRequest, PublishResult


class ArtifactStore(Protocol):
    async def get_bytes(self, uri: str) -> bytes: ...

    async def get_text(self, uri: str, encoding: str = "utf-8") -> str: ...

    async def get_json(self, uri: str) -> Any: ...


class BasePublisher(ABC):
    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store
        self.logger = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    async def publish(self, request: PublishRequest) -> PublishResult:
        raise NotImplementedError

    @staticmethod
    def select_artifact_uri(artifact_uris: Sequence[str], suffixes: Sequence[str]) -> str:
        for artifact_uri in artifact_uris:
            if artifact_uri.lower().endswith(tuple(suffixes)):
                return artifact_uri
        return artifact_uris[0]

    @staticmethod
    def default_label(uri: str) -> str:
        file_name = PurePosixPath(uri).name
        stem = file_name.rsplit(".", maxsplit=1)[0]
        return stem.replace("-", " ").replace("_", " ").title()

    @staticmethod
    def require_metadata(metadata: dict[str, Any], key: str) -> Any:
        value = metadata.get(key)
        if value in (None, ""):
            raise ValueError(f"metadata.{key} is required")
        return value
