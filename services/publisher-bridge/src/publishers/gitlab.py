from __future__ import annotations

import asyncio
import base64
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from models.publish import PublishedArtifact, PublishRequest, PublishResult, PublishTarget
from publishers.base import ArtifactStore, BasePublisher

try:
    import gitlab as gitlab_module
except ImportError:  # pragma: no cover - exercised only in minimal local environments
    gitlab_module = None


class GitLabPublisher(BasePublisher):
    def __init__(self, artifact_store: ArtifactStore, gitlab_client: Any) -> None:
        super().__init__(artifact_store)
        self.gitlab_client = gitlab_client

    async def publish(self, request: PublishRequest) -> PublishResult:
        project_id = str(self.require_metadata(request.metadata, "project_id"))
        base_branch = str(request.metadata.get("base_branch", "main"))
        branch_name = str(
            request.metadata.get("branch_name")
            or f"clawcluster/{request.work_item_id}/{request.task_run_id}"
        )
        commit_message = str(
            request.metadata.get("commit_message")
            or f"Publish approved output for {request.work_item_id}"
        )
        commit_actions = await self._build_commit_actions(request)
        gitlab_result = await asyncio.to_thread(
            self._publish_sync,
            request.target,
            project_id,
            base_branch,
            branch_name,
            commit_message,
            commit_actions,
            request.metadata,
        )

        artifacts = [
            PublishedArtifact(
                kind="branch_ref",
                uri=f"gitlab://{project_id}/branches/{quote(branch_name, safe='')}",
                metadata={
                    "branch_name": branch_name,
                    "base_branch": base_branch,
                    "commit_id": gitlab_result.get("commit_id"),
                },
            )
        ]
        external_refs: dict[str, Any] = {"gitlab_project_id": project_id}

        if request.target == PublishTarget.GITLAB_MR and gitlab_result.get("merge_request_iid") is not None:
            external_refs["gitlab_mr_iid"] = int(gitlab_result["merge_request_iid"])
            artifacts.append(
                PublishedArtifact(
                    kind="mr_ref",
                    uri=f"gitlab://{project_id}/merge_requests/{gitlab_result['merge_request_iid']}",
                    metadata={
                        "branch_name": branch_name,
                        "target_branch": base_branch,
                        "mr_title": gitlab_result.get("merge_request_title"),
                    },
                )
            )

        return PublishResult(
            work_item_id=request.work_item_id,
            task_run_id=request.task_run_id,
            target=request.target,
            success=True,
            status="published",
            message="GitLab publish completed successfully.",
            external_refs=external_refs,
            artifacts=artifacts,
            response_metadata=gitlab_result,
        )

    async def _build_commit_actions(self, request: PublishRequest) -> list[dict[str, Any]]:
        if explicit_actions := request.metadata.get("commit_actions"):
            return await self._resolve_commit_actions(explicit_actions)

        if file_descriptors := request.metadata.get("files"):
            return await self._resolve_commit_actions(file_descriptors)

        derived_actions = await self._actions_from_artifact_manifest(request)
        if derived_actions is not None:
            return derived_actions

        default_directory = str(request.metadata.get("default_target_dir", "published")).strip("/")
        actions: list[dict[str, Any]] = []
        for artifact_uri in request.artifact_uris:
            file_name = PurePosixPath(artifact_uri).name
            content = await self.artifact_store.get_text(artifact_uri)
            actions.append(
                {
                    "action": "create",
                    "file_path": f"{default_directory}/{file_name}",
                    "content": content,
                }
            )
        return actions

    async def _actions_from_artifact_manifest(self, request: PublishRequest) -> list[dict[str, Any]] | None:
        manifest_uri = next((uri for uri in request.artifact_uris if uri.lower().endswith(".json")), None)
        if manifest_uri is None:
            return None
        manifest = await self.artifact_store.get_json(manifest_uri)
        if not isinstance(manifest, dict) or "actions" not in manifest:
            return None
        actions = manifest["actions"]
        if not isinstance(actions, list):
            raise ValueError("GitLab action manifest must contain an actions list")
        return await self._resolve_commit_actions(actions)

    async def _resolve_commit_actions(self, action_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for action_spec in action_specs:
            action = str(action_spec.get("action", "create"))
            file_path = action_spec.get("file_path")
            if not file_path:
                raise ValueError("Each GitLab commit action requires file_path")

            encoding = str(action_spec.get("encoding", "text"))
            content = action_spec.get("content")
            artifact_uri = action_spec.get("artifact_uri")

            if content is None and artifact_uri:
                if encoding == "base64":
                    content = base64.b64encode(await self.artifact_store.get_bytes(artifact_uri)).decode("ascii")
                else:
                    content = await self.artifact_store.get_text(artifact_uri)

            if content is None:
                raise ValueError(f"GitLab commit action for {file_path} is missing content")

            action_payload: dict[str, Any] = {
                "action": action,
                "file_path": file_path,
                "content": content,
            }
            if encoding != "text":
                action_payload["encoding"] = encoding
            if "execute_filemode" in action_spec:
                action_payload["execute_filemode"] = bool(action_spec["execute_filemode"])
            actions.append(action_payload)
        return actions

    def _publish_sync(
        self,
        target: PublishTarget,
        project_id: str,
        base_branch: str,
        branch_name: str,
        commit_message: str,
        commit_actions: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        project = self.gitlab_client.projects.get(project_id)
        self._ensure_branch(project, branch_name, base_branch)
        commit = project.commits.create(
            {
                "branch": branch_name,
                "commit_message": commit_message,
                "actions": commit_actions,
            }
        )

        result: dict[str, Any] = {
            "project_id": str(self._value(project, "id", project_id)),
            "branch_name": branch_name,
            "base_branch": base_branch,
            "commit_id": self._value(commit, "id"),
            "commit_short_id": self._value(commit, "short_id"),
            "commit_web_url": self._value(commit, "web_url"),
        }

        if target == PublishTarget.GITLAB_MR:
            merge_request_title = str(
                metadata.get("mr_title") or f"Publish approved output for {metadata.get('project_id', project_id)}"
            )
            merge_request_description = str(metadata.get("mr_description", ""))
            merge_request = project.mergerequests.create(
                {
                    "source_branch": branch_name,
                    "target_branch": base_branch,
                    "title": merge_request_title,
                    "description": merge_request_description,
                    "remove_source_branch": bool(metadata.get("remove_source_branch", False)),
                    "squash": bool(metadata.get("squash", False)),
                }
            )
            result.update(
                {
                    "merge_request_iid": int(self._value(merge_request, "iid")),
                    "merge_request_title": merge_request_title,
                    "merge_request_web_url": self._value(merge_request, "web_url"),
                }
            )

        return result

    def _ensure_branch(self, project: Any, branch_name: str, base_branch: str) -> None:
        try:
            project.branches.get(branch_name)
            return
        except Exception:
            pass

        try:
            project.branches.create({"branch": branch_name, "ref": base_branch})
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise

    @staticmethod
    def _value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)
