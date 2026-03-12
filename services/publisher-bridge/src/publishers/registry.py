from __future__ import annotations

from collections.abc import Mapping

from models.publish import PublishTarget
from publishers.base import BasePublisher


class PublisherRegistry:
    def __init__(self, publishers: Mapping[PublishTarget, BasePublisher]) -> None:
        self._publishers = dict(publishers)

    def get(self, target: PublishTarget) -> BasePublisher:
        try:
            return self._publishers[target]
        except KeyError as exc:
            raise KeyError(f"No publisher registered for target {target}") from exc
