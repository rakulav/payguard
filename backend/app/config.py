from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    anthropic_api_key: str = ""
    mock_llm: bool = False

    postgres_user: str = "payguard"
    postgres_password: str = "payguard_secret"
    postgres_db: str = "payguard"
    database_url: str = (
        "postgresql+asyncpg://payguard:payguard_secret@postgres:5432/payguard"
    )
    database_url_sync: str = (
        "postgresql://payguard:payguard_secret@postgres:5432/payguard"
    )

    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333

    opensearch_host: str = "opensearch"
    opensearch_port: int = 9200

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings loaded from environment and optional `.env`."""
    return Settings()
