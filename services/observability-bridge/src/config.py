import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr


load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    service_name: str = "observability-bridge"
    port: int = Field(default=8103, ge=1, le=65535)
    langfuse_url: str = Field(min_length=1)
    langfuse_secret_key: SecretStr = Field(min_length=1)
    graphiti_url: str | None = None
    supabase_url: str | None = None
    supabase_service_key: SecretStr | None = None
    worker_jwt_secret: SecretStr = Field(min_length=1)

    @property
    def supabase_dsn(self) -> str:
        if self.supabase_url and self.supabase_url.startswith(("postgresql://", "postgres://")):
            return self.supabase_url

        dsn = os.getenv("SUPABASE_DB_DSN")
        if dsn:
            return dsn

        raise ValueError(
            "asyncpg requires a PostgreSQL DSN. Set SUPABASE_DB_DSN or use a PostgreSQL value in SUPABASE_URL."
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        port=int(os.getenv("PORT", "8103")),
        langfuse_url=os.getenv("LANGFUSE_URL", ""),
        langfuse_secret_key=SecretStr(os.getenv("LANGFUSE_SECRET_KEY", "")),
        graphiti_url=os.getenv("GRAPHITI_URL"),
        supabase_url=os.getenv("SUPABASE_URL"),
        supabase_service_key=(SecretStr(value) if (value := os.getenv("SUPABASE_SERVICE_KEY")) else None),
        worker_jwt_secret=SecretStr(os.getenv("WORKER_JWT_SECRET", "")),
    )
