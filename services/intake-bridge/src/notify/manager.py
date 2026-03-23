from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from src.config import Settings
from src.models.work_item import WorkItem, WorkItemKind


class ManagerNotifier:
    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
        matrix_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self._owns_matrix_client = matrix_client is None
        self._auth_token = settings.manager_auth_token
        self._shared_bucket = settings.minio_hiclaw_bucket
        self._matrix_domain = settings.matrix_domain
        self._matrix_homeserver_url = settings.matrix_homeserver_url
        self._manager_matrix_access_token = settings.manager_matrix_access_token
        self._worker_matrix_access_tokens = settings.worker_matrix_access_tokens
        self._client = client or httpx.AsyncClient(
            base_url=settings.manager_base_url,
            timeout=settings.request_timeout_seconds,
        )
        self._matrix_client = matrix_client or httpx.AsyncClient(
            base_url=self._matrix_homeserver_url,
            timeout=settings.request_timeout_seconds,
        )
        self._manager_user_id: str | None = None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
        if self._owns_matrix_client:
            await self._matrix_client.aclose()

    async def ping(self) -> bool:
        try:
            response = await self._client.post(
                "/tools/invoke",
                headers=self._auth_headers(),
                json={
                    "tool": "sessions_list",
                    "action": "json",
                    "args": {},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return False

        payload = response.json()
        return bool(payload.get("ok"))

    async def notify_work_item(self, work_item: WorkItem, spec_markdown: str) -> dict[str, Any]:
        session_key = f"hook:intake:{work_item.id}"
        worker_name, suggested_worker = self._suggested_worker_target(work_item)
        matrix_room_id = None
        if worker_name and suggested_worker:
            matrix_room_id = await self._ensure_direct_room(worker_name, suggested_worker)
        response = await self._client.post(
            "/hooks/agent",
            headers=self._auth_headers(),
            json={
                "message": self._build_message(
                    work_item,
                    spec_markdown,
                    suggested_worker=suggested_worker,
                    matrix_room_id=matrix_room_id,
                ),
                "name": "Task Intake",
                "sessionKey": session_key,
                "wakeMode": "now",
                "deliver": False,
                "timeoutSeconds": 180,
            },
        )
        response.raise_for_status()
        if response.content:
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    payload.setdefault("sessionKey", session_key)
                    payload.setdefault("endpoint", "/hooks/agent")
                    if suggested_worker:
                        payload.setdefault("worker_target", suggested_worker)
                    if matrix_room_id:
                        payload.setdefault("matrix_room_id", matrix_room_id)
                return payload
            except ValueError:
                return {"status_code": response.status_code, "body": response.text}
        payload = {"status_code": response.status_code, "sessionKey": session_key}
        if suggested_worker:
            payload["worker_target"] = suggested_worker
        if matrix_room_id:
            payload["matrix_room_id"] = matrix_room_id
        return payload

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._auth_token}"}

    def _build_message(
        self,
        work_item: WorkItem,
        spec_markdown: str,
        *,
        suggested_worker: str | None,
        matrix_room_id: str | None,
    ) -> str:
        spec_object_key = f"shared/tasks/task-{work_item.id}/spec.md"
        spec_uri = f"s3://{self._shared_bucket}/{spec_object_key}"
        spec_workspace_path = f"~/hiclaw-fs/shared/tasks/task-{work_item.id}/spec.md"
        work_item_payload = work_item.model_dump_json(indent=2)
        lines = [
            "A new ClawCluster work item was accepted by intake-bridge.",
            "Treat the JSON below as the canonical metadata record for dispatch and orchestration.",
            "The full rendered spec is embedded below; do not block on shared-storage mirroring before planning or delegation.",
            f"Expected mirrored spec path: {spec_workspace_path}",
            f"External spec object: {spec_object_key}",
            f"External spec URI: {spec_uri}",
        ]
        if suggested_worker:
            lines.extend(
                [
                    f"Recommended worker target: {suggested_worker}",
                    f"If you delegate with the message tool, use action `send` and set `target` exactly to `{suggested_worker}`.",
                    "Use that target instead of attempting an unaddressed message.",
                ]
            )
        if matrix_room_id:
            lines.append(f"Established direct room: {matrix_room_id}")
        lines.extend(
            [
                "Delegation requirement: include the full rendered spec markdown in the worker message.",
                "Do not delegate by sending only filesystem or S3 paths; the worker may not have the mirrored spec yet.",
                "",
                "Work item JSON:",
                work_item_payload,
                "",
                "Rendered spec markdown:",
                spec_markdown.rstrip(),
            ]
        )
        return "\n".join(lines)

    def _suggested_worker_target(self, work_item: WorkItem) -> tuple[str | None, str | None]:
        worker_name = {
            WorkItemKind.code_implement: "coding-worker",
            WorkItemKind.code_review: "qa-worker",
            WorkItemKind.workflow_author: "workflow-worker",
            WorkItemKind.plan_breakdown: "planner-worker",
            WorkItemKind.plan_support: "planner-worker",
            WorkItemKind.plan_status: "planner-worker",
            WorkItemKind.knowledge_sync: "knowledge-worker",
            WorkItemKind.qa_validate: "qa-worker",
        }.get(work_item.kind)
        if not worker_name:
            return None, None
        return worker_name, f"@{worker_name}:{self._matrix_domain}"

    async def _ensure_direct_room(self, worker_name: str, worker_user_id: str) -> str | None:
        manager_token = self._manager_matrix_access_token
        worker_token = self._worker_matrix_access_tokens.get(worker_name)
        if not manager_token or not worker_token:
            return None

        manager_user_id = await self._get_manager_user_id(manager_token)
        direct_rooms = await self._get_direct_rooms(manager_user_id, manager_token)
        room_id = self._first_room_id(direct_rooms.get(worker_user_id))
        if room_id is None:
            room_id = await self._create_direct_room(manager_token, worker_user_id)

        await self._join_room(worker_token, room_id)
        await self._ensure_direct_mapping(manager_user_id, manager_token, worker_user_id, room_id)
        await self._ensure_direct_mapping(worker_user_id, worker_token, manager_user_id, room_id)
        return room_id

    async def _get_manager_user_id(self, manager_token: str) -> str:
        if self._manager_user_id:
            return self._manager_user_id

        response = await self._matrix_client.get(
            "/_matrix/client/v3/account/whoami",
            headers=self._matrix_headers(manager_token),
        )
        response.raise_for_status()
        payload = response.json()
        user_id = str(payload.get("user_id") or "").strip()
        if not user_id:
            raise RuntimeError("Matrix whoami did not return a user id for manager")

        self._manager_user_id = user_id
        return user_id

    async def _get_direct_rooms(self, user_id: str, access_token: str) -> dict[str, list[str]]:
        response = await self._matrix_client.get(
            f"/_matrix/client/v3/user/{quote(user_id, safe='')}/account_data/m.direct",
            headers=self._matrix_headers(access_token),
        )
        if response.status_code == 404:
            return {}

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            return {}

        normalized: dict[str, list[str]] = {}
        for peer_user_id, room_ids in payload.items():
            if not isinstance(peer_user_id, str) or not isinstance(room_ids, list):
                continue
            normalized[peer_user_id] = [
                str(room_id).strip()
                for room_id in room_ids
                if isinstance(room_id, str) and str(room_id).strip()
            ]
        return normalized

    async def _create_direct_room(self, manager_token: str, worker_user_id: str) -> str:
        response = await self._matrix_client.post(
            "/_matrix/client/v3/createRoom",
            headers=self._matrix_headers(manager_token),
            json={
                "preset": "trusted_private_chat",
                "is_direct": True,
                "invite": [worker_user_id],
            },
        )
        response.raise_for_status()
        payload = response.json()
        room_id = str(payload.get("room_id") or "").strip()
        if not room_id:
            raise RuntimeError("Matrix createRoom did not return a room_id")
        return room_id

    async def _join_room(self, access_token: str, room_id: str) -> None:
        response = await self._matrix_client.post(
            f"/_matrix/client/v3/rooms/{quote(room_id, safe='')}/join",
            headers=self._matrix_headers(access_token),
            json={},
        )
        if response.is_success:
            return

        error_message = ""
        try:
            payload = response.json()
            error_message = str(payload.get("error") or "")
        except ValueError:
            error_message = response.text

        if response.status_code == 403 and "already joined" in error_message.lower():
            return
        response.raise_for_status()

    async def _ensure_direct_mapping(
        self,
        user_id: str,
        access_token: str,
        peer_user_id: str,
        room_id: str,
    ) -> None:
        direct_rooms = await self._get_direct_rooms(user_id, access_token)
        room_ids = direct_rooms.get(peer_user_id, [])
        if room_id in room_ids:
            return

        direct_rooms[peer_user_id] = [*room_ids, room_id]
        response = await self._matrix_client.put(
            f"/_matrix/client/v3/user/{quote(user_id, safe='')}/account_data/m.direct",
            headers=self._matrix_headers(access_token),
            json=direct_rooms,
        )
        response.raise_for_status()

    def _matrix_headers(self, access_token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    def _first_room_id(self, room_ids: list[str] | None) -> str | None:
        if not room_ids:
            return None
        for room_id in room_ids:
            normalized = str(room_id).strip()
            if normalized:
                return normalized
        return None
