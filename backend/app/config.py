from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./retail_ops.db"
    redis_url: str = "redis://localhost:6379/0"
    chroma_url: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    confidence_review_threshold: float = 0.72
    cors_origins: str = "http://localhost:3000"
    api_keys_json: str = ""
    max_agent_retries: int = 1
    api_rate_limit_per_minute: int = 60
    observability_retention_days: int = 90
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def api_keys(self) -> dict:
        import json

        try:
            value = json.loads(self.api_keys_json or "{}")
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


settings = Settings()