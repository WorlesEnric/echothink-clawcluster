from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus, urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    port: int = Field(
        default=8100,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("PORT", "INTAKE_BRIDGE_PORT"),
    )
    outline_url: str = Field(validation_alias="OUTLINE_URL", min_length=1)
    outline_api_token: SecretStr = Field(validation_alias="OUTLINE_API_TOKEN")
    gitlab_url: str = Field(validation_alias="GITLAB_URL", min_length=1)
    gitlab_token: SecretStr = Field(validation_alias="GITLAB_TOKEN")
    supabase_url: str = Field(validation_alias="SUPABASE_URL", min_length=1)
    supabase_service_key: SecretStr = Field(
        validation_alias=AliasChoices("SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY")
    )
    minio_endpoint: str = Field(validation_alias="MINIO_ENDPOINT", min_length=1)
    minio_access_key: SecretStr = Field(validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: SecretStr = Field(validation_alias="MINIO_SECRET_KEY")
    minio_hiclaw_bucket: str = Field(validation_alias="MINIO_HICLAW_BUCKET", min_length=1)
    hiclaw_manager_port: int | None = Field(
        default=None,
        validation_alias="HICLAW_MANAGER_PORT",
        ge=1,
        le=65535,
    )
    openclaw_manager_url: str | None = Field(default=None, validation_alias="OPENCLAW_MANAGER_URL")
    cluster_name: str = Field(validation_alias="CLUSTER_NAME", min_length=1)
    domain: str = Field(validation_alias="DOMAIN", min_length=1)
    worker_jwt_secret: SecretStr = Field(validation_alias="WORKER_JWT_SECRET")
    connect_timeout_seconds: float = 5.0
    request_timeout_seconds: float = 10.0

    @field_validator(
        "outline_url",
        "gitlab_url",
        "supabase_url",
        "minio_endpoint",
        "openclaw_manager_url",
        "cluster_name",
        "domain",
        mode="before",
    )
    @classmethod
    def _strip_strings(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("Expected string configuration value")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Configuration value cannot be empty")
        return normalized

    @model_validator(mode="after")
    def _validate_manager_configuration(self) -> "Settings":
        if not self.hiclaw_manager_port and not self.openclaw_manager_url:
            raise ValueError("Either HICLAW_MANAGER_PORT or OPENCLAW_MANAGER_URL must be configured")
        return self

    @field_validator(
        "outline_api_token",
        "gitlab_token",
        "supabase_service_key",
        "minio_access_key",
        "minio_secret_key",
        "worker_jwt_secret",
    )
    @classmethod
    def _validate_secret(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().strip()) < 8:
            raise ValueError("Secret values must contain at least 8 characters")
        return value

    @property
    def manager_base_url(self) -> str:
        if self.openclaw_manager_url:
            return self.openclaw_manager_url.rstrip("/")
        assert self.hiclaw_manager_port is not None
        return f"http://hiclaw-manager:{self.hiclaw_manager_port}"

    @property
    def webhook_secret(self) -> str:
        return self.worker_jwt_secret.get_secret_value()

    @property
    def supabase_dsn(self) -> str:
        if self.supabase_url.startswith(("postgresql://", "postgres://")):
            return self.supabase_url

        parsed = urlparse(self.supabase_url)
        host = parsed.hostname or "localhost"
        if host.startswith("supabase."):
            db_host = host.replace("supabase.", "db.", 1)
        elif host.startswith("db."):
            db_host = host
        else:
            db_host = f"db.{host}"

        sslmode = "require" if parsed.scheme == "https" else "disable"
        password = quote_plus(self.supabase_service_key.get_secret_value())
        return f"postgresql://postgres:{password}@{db_host}:5432/postgres?sslmode={sslmode}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
