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
    tuwunel_server_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TUWUNEL_SERVER_NAME", "HICLAW_MATRIX_DOMAIN"),
    )
    hiclaw_manager_port: int | None = Field(
        default=None,
        validation_alias="HICLAW_MANAGER_PORT",
        ge=1,
        le=65535,
    )
    openclaw_manager_url: str | None = Field(default=None, validation_alias="OPENCLAW_MANAGER_URL")
    openclaw_manager_auth_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENCLAW_MANAGER_AUTH_TOKEN", "HICLAW_MANAGER_GATEWAY_KEY"),
    )
    hiclaw_matrix_server: str = Field(
        default="http://hiclaw-manager:6167",
        validation_alias=AliasChoices("HICLAW_MATRIX_SERVER", "MATRIX_HOMESERVER_URL"),
        min_length=1,
    )
    matrix_access_token_manager: SecretStr | None = Field(
        default=None,
        validation_alias="MATRIX_ACCESS_TOKEN_MANAGER",
    )
    matrix_access_token_planner_worker: SecretStr | None = Field(
        default=None,
        validation_alias="MATRIX_ACCESS_TOKEN_PLANNER_WORKER",
    )
    matrix_access_token_workflow_worker: SecretStr | None = Field(
        default=None,
        validation_alias="MATRIX_ACCESS_TOKEN_WORKFLOW_WORKER",
    )
    matrix_access_token_coding_worker: SecretStr | None = Field(
        default=None,
        validation_alias="MATRIX_ACCESS_TOKEN_CODING_WORKER",
    )
    matrix_access_token_qa_worker: SecretStr | None = Field(
        default=None,
        validation_alias="MATRIX_ACCESS_TOKEN_QA_WORKER",
    )
    matrix_access_token_knowledge_worker: SecretStr | None = Field(
        default=None,
        validation_alias="MATRIX_ACCESS_TOKEN_KNOWLEDGE_WORKER",
    )
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
        "hiclaw_matrix_server",
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
        if self.openclaw_manager_auth_token is None:
            raise ValueError(
                "OPENCLAW_MANAGER_AUTH_TOKEN (or HICLAW_MANAGER_GATEWAY_KEY) must be configured"
            )
        return self

    @field_validator(
        "outline_api_token",
        "gitlab_token",
        "supabase_service_key",
        "minio_access_key",
        "minio_secret_key",
        "worker_jwt_secret",
        "openclaw_manager_auth_token",
        "matrix_access_token_manager",
        "matrix_access_token_planner_worker",
        "matrix_access_token_workflow_worker",
        "matrix_access_token_coding_worker",
        "matrix_access_token_qa_worker",
        "matrix_access_token_knowledge_worker",
    )
    @classmethod
    def _validate_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
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
    def manager_auth_token(self) -> str:
        assert self.openclaw_manager_auth_token is not None
        return self.openclaw_manager_auth_token.get_secret_value()

    @property
    def matrix_homeserver_url(self) -> str:
        return self.hiclaw_matrix_server.rstrip("/")

    @property
    def manager_matrix_access_token(self) -> str | None:
        if self.matrix_access_token_manager is None:
            return None
        return self.matrix_access_token_manager.get_secret_value()

    @property
    def worker_matrix_access_tokens(self) -> dict[str, str]:
        token_map = {
            "planner-worker": self.matrix_access_token_planner_worker,
            "workflow-worker": self.matrix_access_token_workflow_worker,
            "coding-worker": self.matrix_access_token_coding_worker,
            "qa-worker": self.matrix_access_token_qa_worker,
            "knowledge-worker": self.matrix_access_token_knowledge_worker,
        }
        return {
            worker_name: token.get_secret_value()
            for worker_name, token in token_map.items()
            if token is not None and token.get_secret_value().strip()
        }

    @property
    def webhook_secret(self) -> str:
        return self.worker_jwt_secret.get_secret_value()

    @property
    def matrix_domain(self) -> str:
        return (self.tuwunel_server_name or self.domain).strip()

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
