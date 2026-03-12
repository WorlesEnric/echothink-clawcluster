import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr


load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    service_name: str = "policy-bridge"
    port: int = Field(default=8102, ge=1, le=65535)
    supabase_url: str | None = None
    supabase_service_key: SecretStr | None = None
    worker_jwt_secret: SecretStr = Field(min_length=1)
    tuwunel_server_name: str | None = None
    matrix_homeserver_url: str = "http://tuwunel.clawcluster.svc:6167"
    matrix_access_token: SecretStr | None = None

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
        port=int(os.getenv("PORT", "8102")),
        supabase_url=os.getenv("SUPABASE_URL"),
        supabase_service_key=(SecretStr(value) if (value := os.getenv("SUPABASE_SERVICE_KEY")) else None),
        worker_jwt_secret=SecretStr(os.getenv("WORKER_JWT_SECRET", "")),
        tuwunel_server_name=os.getenv("TUWUNEL_SERVER_NAME"),
        matrix_homeserver_url=os.getenv("MATRIX_HOMESERVER_URL", "http://tuwunel.clawcluster.svc:6167"),
        matrix_access_token=(SecretStr(value) if (value := os.getenv("MATRIX_ACCESS_TOKEN")) else None),
    )
