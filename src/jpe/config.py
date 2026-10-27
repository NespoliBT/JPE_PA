"""Application configuration loaded from environment variables and ``.env``."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash-1017"


class Settings(BaseSettings):
    """Runtime settings for the assistant.

    Values are read from environment variables (case-insensitive) and from a
    local ``.env`` file. The ``JPE_`` prefix keeps them namespaced.
    """

    model_config = SettingsConfigDict(
        env_prefix="JPE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="OPENROUTER_API_KEY",
        description="OpenRouter API key. Required for any network call.",
    )
    model: str = Field(default=DEFAULT_MODEL, description="Default OpenRouter model slug.")
    base_url: str = Field(
        default=DEFAULT_BASE_URL, description="OpenRouter-compatible API base URL."
    )
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, gt=0)
    system_prompt: str = Field(
        default="You are a helpful, concise personal assistant.",
        description="Base system prompt prepended to every conversation.",
    )

    workspace: str = Field(
        default=".",
        description="Root directory filesystem tools may access.",
    )
    deny_globs: list[str] = Field(
        default_factory=lambda: [
            ".env",
            ".env.*",
            "*.pem",
            "*.key",
            "id_rsa",
            "id_ed25519",
            ".git/config",
            "**/.git/config",
        ],
        description="Glob patterns that filesystem tools must never touch.",
    )
    max_file_bytes: int = Field(
        default=1_000_000,
        gt=0,
        description="Maximum size for file reads and writes.",
    )
    http_timeout: float = Field(
        default=15.0,
        gt=0,
        description="Timeout in seconds for fetch_url requests.",
    )
    allow_shell: bool = Field(
        default=False,
        description="Enable the run_command tool. Off by default.",
    )
    shell_timeout: float = Field(
        default=30.0,
        gt=0,
        description="Timeout in seconds for run_command.",
    )
    soul_path: str = Field(
        default="SOUL.md",
        description="Path to the assistant's identity document (guideline authority).",
    )
    policy_path: str = Field(
        default="POLICY.md",
        description="Path to the governance document (system authority).",
    )
    memory_dir: str = Field(
        default="memory",
        description="Directory holding memory markdown files and the SQLite index.",
    )
    confirmation_ttl_seconds: int = Field(
        default=600,
        gt=0,
        description="How long a tier-3 confirmation token stays valid.",
    )
    mcp_servers: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "MCP servers to connect to, as a JSON list. Each entry needs a "
            "'name' and either 'command' (stdio) or 'url' (http). This is an "
            "allowlist: a server not listed here is never started."
        ),
    )

    def require_api_key(self) -> str:
        """Return the API key or raise a clear error when it is missing."""
        key = self.openrouter_api_key.get_secret_value()
        if not key:
            msg = (
                "OPENROUTER_API_KEY is not set. "
                "Copy .env.example to .env and add your key, or export it in your shell."
            )
            raise RuntimeError(msg)
        return key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
