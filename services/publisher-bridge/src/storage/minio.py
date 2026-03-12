from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config as BotoConfig
from botocore.client import BaseClient


@dataclass(frozen=True, slots=True)
class ArtifactBlob:
    uri: str
    data: bytes
    checksum: str
    size_bytes: int


class MinioArtifactStore:
    def __init__(
        self,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        default_bucket: str,
        client: BaseClient | None = None,
    ) -> None:
        self.default_bucket = default_bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def get_artifact(self, uri: str) -> ArtifactBlob:
        bucket, key = self._parse_s3_uri(uri)
        data = await asyncio.to_thread(self._read_object, bucket, key)
        checksum = hashlib.sha256(data).hexdigest()
        return ArtifactBlob(uri=uri, data=data, checksum=checksum, size_bytes=len(data))

    async def get_bytes(self, uri: str) -> bytes:
        artifact = await self.get_artifact(uri)
        return artifact.data

    async def get_text(self, uri: str, encoding: str = "utf-8") -> str:
        artifact = await self.get_artifact(uri)
        return artifact.data.decode(encoding)

    async def get_json(self, uri: str) -> Any:
        return json.loads(await self.get_text(uri))

    def _read_object(self, bucket: str, key: str) -> bytes:
        response = self._client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def _parse_s3_uri(self, uri: str) -> tuple[str, str]:
        parsed = urlparse(uri)
        if parsed.scheme != "s3":
            raise ValueError(f"Unsupported artifact URI: {uri}")
        bucket = parsed.netloc or self.default_bucket
        key = parsed.path.lstrip("/")
        if not bucket or not key:
            raise ValueError(f"Artifact URI must include bucket and key: {uri}")
        return bucket, key
