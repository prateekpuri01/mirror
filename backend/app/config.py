from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://jobboard:jobboard@db:5432/jobboard"
    redis_url: str = "redis://redis:6379/0"
    # Provider-agnostic LLM config. ``llm_provider`` selects the backend
    # (openai | anthropic | ollama). Anthropic and Ollama are accessed via
    # their OpenAI-compatible endpoints, so all callsites can keep using the
    # AsyncOpenAI SDK — only the base_url + api_key change.
    llm_provider: str = "openai"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434/v1"

    # Per-provider model overrides. Leave blank to use sensible defaults
    # baked into client.py for each provider.
    llm_resume_model: str = ""  # heavy lift: full resume + revisions
    llm_scoring_model: str = ""  # mid: scoring, planning, agent edits
    llm_extraction_model: str = ""  # light: extraction, dedup, parsing
    # Specifically for native web search (Responses API on OpenAI, Messages
    # API w/ web_search tool on Anthropic). Defaults live in
    # web_search_llm.py — currently gpt-5.5 (OpenAI) and claude-opus-4-7
    # (Anthropic), both paired with high reasoning effort.
    llm_web_search_model: str = ""
    # Reasoning effort for OpenAI Responses-API web search. Allowed:
    # "none" | "low" | "medium" | "high" | "xhigh". Default is "high"
    # because the agentic search loop uses chain-of-thought to plan and
    # interpret results; dialing down trades quality for cost.
    llm_web_search_effort: str = ""

    # Perplexity + Brave were retired in v0.2 (2026-05) after the
    # web-search eval showed native LLM search beat Perplexity on every
    # axis and SearXNG handled every query without Brave firing. The
    # decision data lives in backend/scripts/eval/eval_web_search.py.
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
