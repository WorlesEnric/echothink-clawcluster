from __future__ import annotations

import asyncio

import boto3
from botocore.config import Config as BotoConfig

from src.config import Settings


class MinioClient:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.minio_hiclaw_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.minio_endpoint,
            aws_access_key_id=settings.minio_access_key.get_secret_value(),
            aws_secret_access_key=settings.minio_secret_key.get_secret_value(),
            region_name="us-east-1",
            config=BotoConfig(signature_version="s3v4"),
        )

    async def ping(self) -> bool:
        return await asyncio.to_thread(self._head_bucket)

    async def stage_work_item_spec(self, work_item_id: str, markdown: str) -> str:
        key = f"shared/tasks/task-{work_item_id}/spec.md"
        await asyncio.to_thread(self._put_object, key, markdown)
        return key

    def _head_bucket(self) -> bool:
        self._client.head_bucket(Bucket=self._bucket)
        return True

    def _put_object(self, key: str, markdown: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=markdown.encode("utf-8"),
            ContentType="text/markdown; charset=utf-8",
        )
