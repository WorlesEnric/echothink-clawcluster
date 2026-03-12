import logging
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from models.policy import ApprovalRecord, PolicyEvaluationRequest


class MatrixNotifier:
    def __init__(
        self,
        homeserver_url: str,
        access_token: str | None,
        server_name: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._homeserver_url = homeserver_url.rstrip("/")
        self._access_token = access_token
        self._server_name = server_name
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._logger = logging.getLogger(__name__)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def post_approval_request(
        self,
        request: PolicyEvaluationRequest,
        approval_record: ApprovalRecord,
    ) -> None:
        room_id = request.matrix_room_id
        if not room_id:
            self._logger.info(
                "Skipping Matrix approval notification because no room is associated with the work item",
                extra={"work_item_id": request.work_item_id},
            )
            return

        if not self._access_token:
            self._logger.warning(
                "Skipping Matrix approval notification because MATRIX_ACCESS_TOKEN is not configured",
                extra={"work_item_id": request.work_item_id, "room_id": room_id},
            )
            return

        message = self._build_message(request=request, approval_record=approval_record)
        endpoint = (
            f"{self._homeserver_url}/_matrix/client/v3/rooms/"
            f"{quote(room_id, safe='')}/send/m.room.message/{uuid4().hex}"
        )
        response = await self._client.put(
            endpoint,
            headers={"Authorization": f"Bearer {self._access_token}"},
            json={"msgtype": "m.text", "body": message},
        )
        response.raise_for_status()

    def _build_message(self, request: PolicyEvaluationRequest, approval_record: ApprovalRecord) -> str:
        return (
            f"Approval required for work item {request.work_item_id}. "
            f"Risk level={request.risk_level.value}, approval policy={request.approval_policy.value}, "
            f"requested by={request.requested_by}, approval_id={approval_record.id}."
            + (f" Matrix server={self._server_name}." if self._server_name else "")
        )
