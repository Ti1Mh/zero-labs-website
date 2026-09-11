"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Typed, validated application settings (fail-fast on missing values)."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Social Publish API"
    debug: bool = False
    database_url: str 
    cors_origins_raw: str = "http://localhost:3000"
    secret_key: str  # missing => fail-fast; HS256 signing key
    redis_url: str = "redis://127.0.0.1:6379/0"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 30
    otp_ttl_minutes: int = 5
    otp_hourly_cap: int = 3
    fernet_key: str | None = None

    # S3 / MinIO Object Storage
    s3_endpoint_url: str = "http://127.0.0.1:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket_name: str = "social-publish"
    s3_region_name: str = "us-east-1"
    s3_public_url: str | None = None

    # AI & OpenRouter Configuration
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "https://zeroio.io"
    openrouter_app_name: str = "ZeroIO Social Studio"
    ai_free_model: str = "google/gemini-2.0-flash:free"
    ai_pro_model: str = "openai/gpt-4o-mini"
    ai_enterprise_model: str = "anthropic/claude-3.5-sonnet"
    ai_default_provider: str = "mock"
    ai_request_timeout: float = 35.0

    @property
    def cors_origins(self) -> list[str]:
        """Split the comma-separated CORS origins string."""
        return [o.strip() for o in self.cors_origins_raw.split(",") if o.strip()]
    
    @property
    def encryption_key(self) -> bytes:
        """Return the Fernet key; fail fast if not configured."""
        if not self.fernet_key:
            raise RuntimeError("FERNET_KEY must be set in environment for encryption")
        return self.fernet_key.encode("utf-8")

@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached Settings instance."""
    return Settings()