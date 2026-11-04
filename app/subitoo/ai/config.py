"""Independent AI_ configuration; credentials never appear in UI settings."""

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AIConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_", env_file=".env", extra="ignore")
    enabled: bool = False
    # Legacy is retained for old deployments/tests; this app selects on_demand.
    workflow: Literal["legacy", "on_demand"] = "legacy"
    worth_ttl_hours: int = Field(default=24, ge=1, le=168)
    provider: Literal["local", "openai", "hermes"] = "local"
    hermes_python: str = ""
    hermes_home: str = ""
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    endpoint: str = "http://127.0.0.1:8080/v1"
    model: str = "underdog-saluki-27b-iq2"
    api_key: SecretStr = SecretStr("")
    timeout: float = Field(default=300, ge=1, le=300)
    retries: int = Field(default=1, ge=0, le=2)
    structured_output: bool = True
    thinking: bool = False
    max_tokens: int = Field(default=2500, ge=128, le=8192)
    max_response_bytes: int = Field(default=1000000, ge=1024, le=4000000)
    max_context_chars: int = Field(default=48000, ge=2000, le=200000)
    search_backend: str = "ddgs"
    searxng_url: str = ""
    search_limit: int = Field(default=5, ge=1, le=10)
    max_sources: int = Field(default=5, ge=1, le=10)
    fetch_timeout: float = Field(default=15, ge=1, le=60)
    max_page_bytes: int = Field(default=1000000, ge=1024, le=4000000)
    poll_seconds: float = Field(default=0.8, ge=0.1, le=300)
    discover_seconds: int = Field(default=60, ge=1, le=3600)
    discovery_batch_size: int = Field(default=5, ge=1, le=100)
    identification_backlog: int = Field(default=10, ge=1, le=1000)
    research_ttl_days: int = Field(default=90, ge=1, le=3650)
    max_attempts: int = Field(default=3, ge=1, le=5)
    max_pending_jobs: int = Field(default=1000, ge=1, le=10000)
    stale_seconds: int = Field(default=1800, ge=60, le=86400)
    lock_path: str = ".ai-inference.lock"

    @model_validator(mode="after")
    def provider_defaults(self):
        if self.provider in {"openai", "hermes"}:
            if "endpoint" not in self.model_fields_set:
                self.endpoint = "https://api.openai.com/v1"
            if "model" not in self.model_fields_set:
                self.model = "gpt-6.1-sol"
        return self

    def configuration_error(self):
        if self.provider == "openai" and not self.api_key.get_secret_value():
            return "Configura AI_API_KEY sul backend per usare GPT."
        if self.provider == "hermes":
            if (
                not self.hermes_python
                or not Path(self.hermes_python).is_absolute()
                or not os.access(self.hermes_python, os.X_OK)
            ):
                return "Configura il percorso Python del runtime Hermes sul backend."
            if (
                not self.hermes_home
                or not Path(self.hermes_home).is_absolute()
                or not (Path(self.hermes_home) / "auth.json").is_file()
            ):
                return "Configura il profilo Hermes autenticato sul backend."
        return None

    def public(self):
        return {
            "enabled": self.enabled,
            "workflow": self.workflow,
            "model": self.model,
            "provider": self.provider,
            "reasoning_effort": self.reasoning_effort
            if self.provider in {"openai", "hermes"}
            else None,
            "credential_configured": self.configuration_error() is None
            if self.provider == "hermes"
            else bool(self.api_key.get_secret_value()),
            "configuration_error": self.configuration_error(),
            "search_backend": self.search_backend,
            "research_ttl_days": self.research_ttl_days,
            "weights": {},
        }


def get_config():
    return AIConfig()
