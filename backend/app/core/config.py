from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Market Intelligence & Research Platform"
    app_version: str = "1.0.0"
    environment: Literal["dev", "test", "prod"] = "dev"
    api_prefix: str = "/api"

    database_url: str = "sqlite+aiosqlite:///./market_intel.db"

    llm_provider: Literal["heuristic", "openai", "anthropic", "openai_compatible"] = "heuristic"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_max_tokens: int = 1500
    llm_temperature: float = 0.2

    embedding_provider: Literal["local", "openai"] = "local"
    embedding_model: str = "text-embedding-3-small"

    http_user_agent: str = "AIMarketIntelligence-Edu/1.0 (educational research project)"
    http_timeout_seconds: float = 20.0
    source_max_retries: int = 2
    source_retry_backoff: float = 1.5

    cache_market_ttl: int = 900
    cache_fundamentals_ttl: int = 43200
    cache_news_ttl: int = 1800
    cache_company_ttl: int = 86400

    research_max_news_articles: int = 20
    research_max_sources_per_report: int = 40
    research_max_agent_iterations: int = 6
    research_depth: Literal["quick", "standard", "deep"] = "standard"
    research_dedup_ttl: int = 3600
    research_concurrency: int = 4

    rate_limit_per_minute: int = 60

    log_level: str = "INFO"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"])

    max_upload_size_mb: int = 10
    allowed_upload_extensions: list[str] = Field(default_factory=lambda: [".txt", ".md", ".pdf"])

    @property
    def is_llm_configured(self) -> bool:
        return self.llm_provider != "heuristic" and bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
