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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()