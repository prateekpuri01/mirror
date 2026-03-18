"""Module-level scrape progress tracker.

Single-user, single-process app — one global instance is fine.
"""

import time


class ScrapeProgress:
    def __init__(self) -> None:
        self.active: bool = False
        self.company_name: str = ""
        self.phase: str = ""  # "resolving" | "listing" | "fetching" | "done"
        self.found: int = 0
        self.total: int | None = None
        self._started_at: float = 0

    def start(self, company_name: str = "") -> None:
        self.active = True
        self.company_name = company_name
        self.phase = "resolving"
        self.found = 0
        self.total = None
        self._started_at = time.monotonic()

    def listing(self) -> None:
        self.phase = "listing"

    def fetching(self, total: int) -> None:
        self.total = total
        self.phase = "fetching"

    def increment(self) -> None:
        self.found += 1

    def finish(self) -> None:
        self.active = False
        self.phase = "done"

    def to_dict(self) -> dict:
        elapsed = time.monotonic() - self._started_at if self.active else 0
        return {
            "active": self.active,
            "company_name": self.company_name,
            "phase": self.phase,
            "found": self.found,
            "total": self.total,
            "elapsed_seconds": round(elapsed, 1),
        }


progress = ScrapeProgress()
