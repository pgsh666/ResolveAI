from functools import lru_cache
from pathlib import Path
import os
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "ResolveAI"
    app_env: Literal["development", "test", "production"] = "development"
    api_prefix: str = "/api/v1"
    auth_enabled: bool = True
    auth_cookie_secure: bool = False
    auth_session_hours: int = Field(default=8, ge=1, le=168)
    auth_bootstrap_users: list[dict[str, str]] = Field(default=[], repr=False)
    public_deployment: bool = False
    agent_mode: Literal["demo", "openai", "deepseek"] = "demo"
    openai_api_key: str | None = Field(default=None, repr=False)
    openai_model: str = "gpt-5-mini"
    openai_fast_model: str = "gpt-5-nano"
    openai_embedding_model: str = "text-embedding-3-small"
    deepseek_api_key: str | None = Field(default=None, repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_fast_model: str = "deepseek-flash"
    noncritical_model_stages: str = "triage,rewrite"
    model_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    model_max_retries: int = Field(default=2, ge=0, le=5)
    tool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    tool_max_retries: int = Field(default=2, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.2, ge=0, le=5)
    rag_min_score: float = Field(default=0.15, ge=0, le=1)
    quality_max_regenerations: int = Field(default=1, ge=0, le=3)
    failure_escalation_threshold: int = Field(default=2, ge=1, le=10)
    embedding_dimensions: int = Field(default=256, ge=64, le=2000)
    embedding_provider: Literal["auto", "local", "fastembed", "openai"] = "auto"
    semantic_embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_cache_dir: str = str(BACKEND_DIR.parent / ".build" / "embedding-models")
    semantic_rag_min_score: float = Field(default=0.55, ge=0, le=1)
    prompt_version: str = "resolveai-prompts-2026-10-18-customer-tone-v1"
    model_input_cost_per_million_usd: float = Field(default=0.0, ge=0)
    model_output_cost_per_million_usd: float = Field(default=0.0, ge=0)
    fast_model_input_cost_per_million_usd: float = Field(default=0.0, ge=0)
    fast_model_output_cost_per_million_usd: float = Field(default=0.0, ge=0)
    model_primary_max_output_tokens: int = Field(default=1600, ge=128, le=8192)
    model_fast_max_output_tokens: int = Field(default=600, ge=128, le=4096)
    model_max_context_chars: int = Field(default=12000, ge=1000, le=100000)
    agent_max_conversation_messages: int = Field(default=8, ge=1, le=50)
    agent_max_message_chars: int = Field(default=1200, ge=100, le=10000)
    rag_max_documents: int = Field(default=3, ge=1, le=10)
    rag_snippet_chars: int = Field(default=240, ge=80, le=1000)
    retrieval_cache_enabled: bool = True
    retrieval_cache_ttl_seconds: int = Field(default=300, ge=10, le=86400)
    retrieval_cache_max_entries: int = Field(default=1000, ge=10, le=10000)
    performance_sample_window: int = Field(default=1000, ge=50, le=10000)
    rate_limit_enabled: bool = False
    rate_limit_requests_per_minute: int = Field(default=240, ge=1, le=100000)
    max_concurrent_requests: int = Field(default=32, ge=1, le=1000)
    request_queue_timeout_seconds: float = Field(default=2.0, gt=0, le=60)
    otel_enabled: bool = False
    otel_service_name: str = "resolveai-backend"
    otel_exporter_otlp_traces_endpoint: str | None = None
    otel_export_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    otel_sample_ratio: float = Field(default=1.0, ge=0, le=1)
    otel_console_exporter: bool = False
    otel_ui_url: str = "http://localhost:16686"
    async_tasks_enabled: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"
    task_queue_name: str = "resolveai"
    task_default_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    task_evaluation_timeout_seconds: int = Field(default=1800, ge=60, le=7200)
    task_result_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    task_failure_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    task_evaluation_output_dir: str = str(BACKEND_DIR / "evaluation" / "reports" / "tasks")
    database_url: str = "sqlite:///./resolveai.db"
    cors_origins: str = "http://localhost:3000"
    mcp_enabled: bool = False
    mcp_server_url: str = "http://127.0.0.1:8001/mcp"
    mcp_server_host: str = "127.0.0.1"
    mcp_server_port: int = Field(default=8001, ge=1, le=65535)
    tool_approval_secret: str = Field(
        default="resolveai-local-development-secret",
        min_length=24,
        repr=False,
        description="Shared HMAC secret used to sign short-lived write-tool approval credentials",
    )

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env",) if os.environ.get("APP_ENV") == "test" else (BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env.auth"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_openai_mode(self) -> "Settings":
        if not self.auth_enabled and self.app_env != "test":
            raise ValueError("Authentication can only be disabled in the isolated test environment")
        if self.public_deployment and (not self.auth_cookie_secure or any(not origin.startswith("https://") for origin in self.cors_origin_list)):
            raise ValueError("Public deployment requires HTTPS origins and Secure session cookies")
        if self.public_deployment and (self.app_env != "production" or not self.auth_enabled or len(self.tool_approval_secret) < 40 or "resolveai" in self.tool_approval_secret):
            raise ValueError("Public deployment requires production authentication and a strong non-default signing secret")
        if self.public_deployment:
            from sqlalchemy.engine import make_url
            address = make_url(self.database_url)
            if not self.is_postgres or len(address.password or "") < 20:
                raise ValueError("Public deployment requires PostgreSQL with a strong private database password")
            if not self.cors_origin_list or any(not urlsplit(origin).hostname or "*" in origin or urlsplit(origin).username
                                               or urlsplit(origin).path or urlsplit(origin).query or urlsplit(origin).fragment
                                               for origin in self.cors_origin_list):
                raise ValueError("Public deployment requires exact HTTPS origins without paths or wildcards")
        if self.agent_mode == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when AGENT_MODE=openai")
        if self.agent_mode == "deepseek" and not self.deepseek_api_key:
            raise ValueError("DEEPSEEK_API_KEY is required when AGENT_MODE=deepseek")
        return self

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def active_model(self) -> str:
        if self.agent_mode == "deepseek":
            return self.deepseek_model
        if self.agent_mode == "openai":
            return self.openai_model
        return "deterministic-demo"

    @property
    def noncritical_stage_set(self) -> set[str]:
        return {
            item.strip().lower()
            for item in self.noncritical_model_stages.split(",")
            if item.strip()
        }

    def model_for_stage(self, stage: str | None = None) -> str:
        if self.agent_mode == "demo":
            return "deterministic-demo"
        economy = bool(stage and stage.lower() in self.noncritical_stage_set)
        if self.agent_mode == "deepseek":
            return self.deepseek_fast_model if economy else self.deepseek_model
        return self.openai_fast_model if economy else self.openai_model

    def model_costs_for_stage(self, stage: str | None = None) -> tuple[float, float]:
        if stage and stage.lower() in self.noncritical_stage_set:
            return (
                self.fast_model_input_cost_per_million_usd,
                self.fast_model_output_cost_per_million_usd,
            )
        return (
            self.model_input_cost_per_million_usd,
            self.model_output_cost_per_million_usd,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
