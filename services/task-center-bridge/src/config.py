from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote_plus, urlparse

from pydantic import AliasChoices, Field, SecretStr, field_validator
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
        default=8104,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("PORT", "TASK_CENTER_BRIDGE_PORT"),
    )
    cluster_name: str = Field(validation_alias="CLUSTER_NAME", min_length=1)
    domain: str = Field(validation_alias="DOMAIN", min_length=1)
    supabase_url: str = Field(validation_alias=AliasChoices("SUPABASE_DB_DSN", "SUPABASE_URL"), min_length=1)
    supabase_service_key: SecretStr = Field(
        validation_alias=AliasChoices("SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY")
    )
    intake_bridge_url: str = Field(
        default="http://intake-bridge:8100",
        validation_alias=AliasChoices("INTAKE_BRIDGE_URL", "CLAWCLUSTER_INTAKE_BRIDGE_URL"),
        min_length=1,
    )
    taskcenter_callback_url: str = Field(
        default="http://taskcenter:8000/api/v1/webhooks/clawcluster",
        validation_alias=AliasChoices("TASKCENTER_CALLBACK_URL", "TASK_CENTER_CALLBACK_URL"),
        min_length=1,
    )
    taskcenter_callback_bearer_token: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("TASKCENTER_CALLBACK_BEARER_TOKEN", "TASK_CENTER_CALLBACK_BEARER_TOKEN"),
    )
    connect_timeout_seconds: float = Field(default=5.0, ge=0.1)
    request_timeout_seconds: float = Field(default=15.0, ge=0.1)
    reconcile_interval_seconds: float = Field(default=5.0, ge=0.5)
    outbox_interval_seconds: float = Field(default=3.0, ge=0.5)
    reconcile_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_batch_size: int = Field(default=50, ge=1, le=500)

    @field_validator("cluster_name", "domain", "supabase_url", "intake_bridge_url", "taskcenter_callback_url", mode="before")
    @classmethod
    def _strip_required_strings(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("Expected string configuration value")
        normalized = value.strip()
        if not normalized:
            raise ValueError("Configuration value cannot be empty")
        return normalized

    @field_validator("supabase_service_key")
    @classmethod
    def _validate_supabase_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value().strip()) < 8:
            raise ValueError("SUPABASE service key must contain at least 8 characters")
        return value

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

    @property
    def intake_bridge_base_url(self) -> str:
        return self.intake_bridge_url.rstrip("/")

    @property
    def taskcenter_callback_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.taskcenter_callback_bearer_token and self.taskcenter_callback_bearer_token.get_secret_value().strip():
            headers["Authorization"] = f"Bearer {self.taskcenter_callback_bearer_token.get_secret_value().strip()}"
        return headers


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
