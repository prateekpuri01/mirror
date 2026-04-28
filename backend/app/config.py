from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://jobboard:jobboard@db:5432/jobboard"
    redis_url: str = "redis://redis:6379/0"
    openai_api_key: str = ""
    perplexity_api_key: str = ""  # Optional: premium web search for company research
    brave_api_key: str = ""  # Optional: alternative web search
    searxng_url: str = "http://searxng:8080"
    hot_search_model: str = "gpt-4o-mini"
    # Browser-agent (Playwright-driven careers-page drill) is the slowest
    # path in the hot-search pipeline — 50s timeout per candidate × dozens
    # of non-ATS companies per run dominates wall time. Off by default;
    # flip to True via HOT_SEARCH_BROWSER_AGENT=1 for one-off broad
    # searches where latency is acceptable.
    hot_search_browser_agent: bool = False
    profile_yaml_path: str = "/app/docs/profile.yaml"
    profile_complete_yaml_path: str = "/app/docs/profile_complete.yaml"

    @property
    def sync_database_url(self) -> str:
        """Alembic needs a sync URL."""
        return self.database_url.replace("+asyncpg", "")

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
