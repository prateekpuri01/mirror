"""In-memory progress tracker for onboarding URL crawling."""

import time
from dataclasses import dataclass, field


@dataclass
class OnboardingProgress:
    task_id: str | None = None
    active: bool = False
    total_urls: int = 0
    completed_urls: int = 0
    results: dict[str, dict] = field(default_factory=dict)
    status: str = "idle"  # idle | running | completed | failed
    error: str | None = None
    started_at: float | None = None

    def start(self, task_id: str, total_urls: int) -> None:
        self.task_id = task_id
        self.active = True
        self.total_urls = total_urls
        self.completed_urls = 0
        self.results = {}
        self.status = "running"
        self.error = None
        self.started_at = time.time()

    def url_completed(self, url: str, result: dict) -> None:
        self.results[url] = result
        self.completed_urls += 1
        if self.completed_urls >= self.total_urls:
            self.finish()

    def finish(self) -> None:
        self.active = False
        self.status = "completed"

    def fail(self, error: str) -> None:
        self.active = False
        self.status = "failed"
        self.error = error

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "total_urls": self.total_urls,
            "completed_urls": self.completed_urls,
            "results": self.results,
            "error": self.error,
        }


# Singleton instance — single-user app
progress = OnboardingProgress()
