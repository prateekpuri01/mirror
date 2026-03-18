from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://jobboard:jobboard@db:5432/jobboard"
    redis_url: str = "redis://redis:6379/0"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    brave_api_key: str = ""
    tavily_api_key: str = ""
    hot_search_model: str = "gpt-4o-mini"
    profile_yaml_path: str = "/app/docs/profile.yaml"
    profile_complete_yaml_path: str = "/app/docs/profile_complete.yaml"

    @property
    def sync_database_url(self) -> str:
        """Alembic needs a sync URL."""
        return self.database_url.replace("+asyncpg", "")

    model_config = {"env_file": ".env"}


settings = Settings()
