from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest import mock
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from models.publish import PublishRequest, PublishTarget  # noqa: E402


class FakeArtifactStore:
    def __init__(self, artifacts: dict[str, bytes | str | dict[str, Any]]) -> None:
        self._artifacts = artifacts

    async def get_bytes(self, uri: str) -> bytes:
        payload = self._artifacts[uri]
        if isinstance(payload, bytes):
            return payload
        if isinstance(payload, str):
            return payload.encode("utf-8")
        return json.dumps(payload).encode("utf-8")

    async def get_text(self, uri: str, encoding: str = "utf-8") -> str:
        return (await self.get_bytes(uri)).decode(encoding)

    async def get_json(self, uri: str) -> Any:
        payload = self._artifacts[uri]
        if isinstance(payload, dict):
            return payload
        return json.loads(await self.get_text(uri))


@pytest.fixture
def artifact_store_factory() -> type[FakeArtifactStore]:
    return FakeArtifactStore


@pytest.fixture
def make_publish_request():
    def _make_publish_request(
        *,
        target: PublishTarget,
        artifact_uris: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> PublishRequest:
        return PublishRequest(
            work_item_id="wi_test_publish",
            task_run_id=uuid4(),
            target=target,
            artifact_uris=artifact_uris,
            metadata=metadata or {},
        )

    return _make_publish_request


class SimpleMocker:
    def __init__(self) -> None:
        self._patches: list[tuple[object, str, object]] = []

    def spy(self, obj: object, attribute: str) -> mock.Mock:
        original = getattr(obj, attribute)
        wrapper = mock.Mock(wraps=original)
        setattr(obj, attribute, wrapper)
        self._patches.append((obj, attribute, original))
        return wrapper

    def stopall(self) -> None:
        for obj, attribute, original in reversed(self._patches):
            setattr(obj, attribute, original)
        self._patches.clear()


@pytest.fixture
def mocker() -> SimpleMocker:
    mocker_fixture = SimpleMocker()
    try:
        yield mocker_fixture
    finally:
        mocker_fixture.stopall()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "asyncio: mark test as asyncio-compatible")


def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:
    if "asyncio" not in pyfuncitem.keywords:
        return None

    test_function = pyfuncitem.obj
    if not asyncio.iscoroutinefunction(test_function):
        return None

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        kwargs = {
            key: value
            for key, value in pyfuncitem.funcargs.items()
            if key in pyfuncitem._fixtureinfo.argnames
        }
        loop.run_until_complete(test_function(**kwargs))
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    return True
