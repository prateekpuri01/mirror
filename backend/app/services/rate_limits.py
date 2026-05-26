"""Centralized concurrency limits derived from the user's API rate limits.

On startup (or after key test at /setup), the detected rate limits are stored
here. All semaphores across the app reference these values so concurrency
automatically scales to the user's API tier.

Default values are conservative (free-tier safe). If the user's key test
detects higher limits, they're bumped up.
"""

import logging

logger = logging.getLogger(__name__)

# Defaults (conservative: assumes a low-tier key until test-key detects otherwise)
_config = {
    "requests_per_minute": 60,
    "tokens_per_minute": 60_000,
}


def update_from_test(detected: dict) -> None:
    """Update limits from the key test response headers."""
    if detected.get("requests_per_minute"):
        _config["requests_per_minute"] = detected["requests_per_minute"]
    if detected.get("tokens_per_minute"):
        _config["tokens_per_minute"] = detected["tokens_per_minute"]
    logger.info(
        "Rate limits updated: %d RPM, %d TPM",
        _config["requests_per_minute"],
        _config["tokens_per_minute"],
    )


def get_limits() -> dict:
    """Return current limits."""
    return dict(_config)


def max_concurrent_llm_calls() -> int:
    """How many LLM calls can run in parallel.

    Heuristic: allow up to RPM/10 concurrent calls (each call takes ~3-10s,
    so 10 calls/min leaves headroom). Clamped to [2, 20].
    """
    rpm = _config["requests_per_minute"]
    concurrent = max(2, min(20, rpm // 10))
    return concurrent


def max_concurrent_scoring() -> int:
    """How many scoring jobs can run in parallel.

    Each scoring job makes 2 LLM calls (role_fit + interest_fit).
    So we allow half of max_concurrent_llm_calls.
    """
    return max(1, max_concurrent_llm_calls() // 2)


def max_concurrent_browser() -> int:
    """How many browser tabs can run in parallel.

    Browser tabs are memory-heavy; keep this low. The LLM call after
    each fetch is the real bottleneck anyway.
    """
    return max(1, min(5, max_concurrent_llm_calls() // 3))
