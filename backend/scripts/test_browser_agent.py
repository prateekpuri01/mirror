"""Isolated robustness tests for the browser-agent fallback.

Runs `_crawl_careers_page_for_job` directly on a list of known-tricky careers
pages (Google, IBM, Cox, BlackLine, etc.) so we can measure how much the
accumulate-and-fallback changes moved the needle — without waiting for a full
hot-search to scan 60 companies.

Usage (from host):
    docker compose exec api python -m scripts.test_browser_agent
    docker compose exec api python -m scripts.test_browser_agent --only google,ibm
    docker compose exec api python -m scripts.test_browser_agent --verbose

Each case is scored as:
    ✅ IMPORTED — fallback (or agent) produced a CompanyHit
    ⚪ NO_HIT   — agent exhausted rounds, fallback found nothing (legit bare lead)
    ❌ ERROR    — exception / timeout / browser crash
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field


@dataclass
class CapturedLogs:
    """Collects log messages from hot_company_search for one test case."""

    messages: list[str] = field(default_factory=list)

    def handle(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    def count(self, needle: str) -> int:
        return sum(1 for m in self.messages if needle in m)

    def first_matching(self, needle: str) -> str | None:
        return next((m for m in self.messages if needle in m), None)


class _CaptureHandler(logging.Handler):
    """Routes log records to a CapturedLogs object (settable at runtime)."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.target: CapturedLogs | None = None

    def emit(self, record: logging.LogRecord) -> None:
        if self.target is not None:
            self.target.handle(record)


# Careers-page URLs that previously produced BARE LEADS during hot searches.
# Guidance is kept generic so we're testing the browser agent, not relevance scoring.
# (company_name, careers_url, guidance)
CASES: list[tuple[str, str, str]] = [
    # Enterprise Workday/custom portals — previously all bare leads
    ("IBM", "https://www.ibm.com/careers/search", "machine learning engineer"),
    ("Cox Enterprises", "https://jobs.coxenterprises.com/", "data scientist"),
    ("Booz Allen", "https://careers.boozallen.com/", "data scientist"),
    ("BlackLine", "https://www.blackline.com/careers/", "machine learning engineer"),
    ("Accenture", "https://www.accenture.com/us-en/careers", "data engineer"),
    ("PwC", "https://jobs.us.pwc.com/", "data scientist"),
    # Custom SPAs — previously bare leads
    ("Automata", "https://careers.automata.tech/jobs", "software engineer"),
    ("Flock Safety", "https://flocksafety.com/careers", "machine learning engineer"),
    ("Tempus AI", "https://www.tempus.com/careers/", "machine learning engineer"),
    # Big-tech SPAs
    (
        "Google",
        "https://www.google.com/about/careers/applications/jobs/results/",
        "machine learning engineer",
    ),
    ("Apple", "https://jobs.apple.com/en-us/search", "machine learning engineer"),
    # Known-good control: standard careers page with ATS-style URLs
    ("Anthropic", "https://www.anthropic.com/careers", "machine learning engineer"),
]


PROFILE_KEYWORDS: dict = {
    "role_titles": {"machine learning engineer", "data scientist", "data engineer"},
    "domains": {"AI", "machine learning"},
    "tech_keywords": ["python", "pytorch", "tensorflow", "machine learning"],
}


async def run_one(
    name: str,
    url: str,
    guidance: str,
    capture_handler: _CaptureHandler,
    verbose: bool = False,
) -> dict:
    """Invoke the browser agent on a single URL. Return a result dict."""
    # Local import so logging setup can happen first
    from app.services.hot_company_search import _crawl_careers_page_for_job

    captured = CapturedLogs()
    capture_handler.target = captured

    result: dict = {
        "name": name,
        "url": url,
        "guidance": guidance,
        "hits": [],
        "elapsed": 0.0,
        "error": None,
        "logs": captured,
    }

    start = time.time()
    try:
        hits = await asyncio.wait_for(
            _crawl_careers_page_for_job(
                company_name=name,
                careers_url=url,
                guidance=guidance,
                profile_keywords=PROFILE_KEYWORDS,
                locations=None,
                min_salary=None,
            ),
            timeout=120,
        )
        result["hits"] = hits
    except TimeoutError:
        result["error"] = "TIMEOUT (>120s)"
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        if verbose:
            result["traceback"] = traceback.format_exc()
    finally:
        result["elapsed"] = time.time() - start
        capture_handler.target = None

    # Classify outcome from captured logs
    logs = captured
    strict_count = 0
    relaxed_count = 0
    for m in logs.messages:
        # Format 1: "... N strict job links (+M relaxed) from T total this round"
        #           (emitted per round when extract_job_links finds strict matches)
        if "strict job links" in m and "relaxed" in m:
            try:
                parts = m.split(":")[-1].strip()
                strict = int(parts.split(" strict")[0].strip())
                relaxed = int(parts.split("+")[1].split(" relaxed")[0].strip())
                strict_count = max(strict_count, strict)
                relaxed_count = max(relaxed_count, relaxed)
            except (ValueError, IndexError):
                pass
        # Format 2: "pre-extract found N strict + M relaxed job links"
        if "pre-extract found" in m:
            try:
                tail = m.split("pre-extract found")[-1].strip()
                strict = int(tail.split(" strict")[0].strip())
                relaxed = int(tail.split("+")[1].split(" relaxed")[0].strip())
                strict_count = max(strict_count, strict)
                relaxed_count = max(relaxed_count, relaxed)
            except (ValueError, IndexError):
                pass
        # Format 3: "fallback for ... trying N candidates (S strict, R relaxed)"
        if "fallback for" in m and "candidates" in m:
            try:
                inside = m.split("(")[-1].split(")")[0]
                s = int(inside.split(" strict")[0].strip())
                r = int(inside.split(",")[-1].split(" relaxed")[0].strip())
                strict_count = max(strict_count, s)
                relaxed_count = max(relaxed_count, r)
            except (ValueError, IndexError):
                pass

    result["strict_links"] = strict_count
    result["relaxed_links"] = relaxed_count
    result["picked_job"] = logs.count("LLM picked job") > 0
    result["import_dedup"] = logs.count("Job URL already in database") > 0
    result["import_succeeded"] = (
        logs.count("SUCCEEDED for") > 0 or logs.count("fallback SUCCEEDED for") > 0
    )
    result["used_fallback"] = logs.count("fallback for") > 0

    return result


def classify(r: dict) -> tuple[str, str]:
    """Return (emoji_status, outcome_label) for a result dict.

    Outcome ladder (best → worst):
      ✅ IMPORTED   — browser agent or fallback imported a new job
      🟢 AGENT_OK   — agent extracted + picked a job but it was a DB dup
      🟡 PICKED_NO_IMPORT — picked a job but import failed (non-dedup reason)
      🟠 LINKS_ONLY — extracted strict/relaxed links but nothing was picked/imported
      ⚪ NO_LINKS   — agent ran to completion but found no job-like URLs
      ❌ ERROR      — exception / timeout
    """
    if r["error"]:
        return "❌", "ERROR"
    if r["import_succeeded"]:
        return "✅", "IMPORTED"
    if r["picked_job"] and r["import_dedup"]:
        return "🟢", "AGENT_OK (dup in DB)"
    if r["picked_job"]:
        return "🟡", "PICKED_NO_IMPORT"
    if r["strict_links"] > 0 or r["relaxed_links"] > 0:
        return "🟠", "LINKS_ONLY"
    return "⚪", "NO_LINKS"


def format_result(r: dict) -> str:
    emoji, label = classify(r)
    status = f"{emoji} {label}  ({r['elapsed']:.1f}s)"

    if r["error"]:
        return f"{status}\n  {r['error']}"

    lines = [status]
    lines.append(
        f"  links: {r['strict_links']} strict, {r['relaxed_links']} relaxed"
        + (" (via fallback)" if r["used_fallback"] else "")
    )
    if r["hits"]:
        hit = r["hits"][0]
        top = hit.top_jobs[0] if hit.top_jobs else {}
        lines.append(f"  → {top.get('title', '?')[:70]}")
        lines.append(f"    {top.get('url', '?')}")
    return "\n".join(lines)


async def run_diag_mode(args: argparse.Namespace) -> None:
    """Skip the agent entirely — just load the page, call extract_job_links,
    and dump what it finds. Used to compare raw extractor output against
    real-world pages without paying for LLM calls.
    """
    import json as _json

    from app.ai.browser_tools import PlaywrightToolExecutor
    from app.services.browser_pool import _ensure_browser

    cases = CASES
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",") if s.strip()}
        cases = [c for c in CASES if any(w in c[0].lower() for w in wanted)]

    browser = await _ensure_browser()
    context = await browser.new_context(ignore_https_errors=True)

    try:
        for name, url, _guidance in cases:
            print(f"\n=== {name} ===")
            print(f"  URL: {url}")
            page = await context.new_page()
            try:
                start = time.time()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=8000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
                except Exception as e:
                    print(f"  ❌ navigate failed: {e}")
                    continue

                executor = PlaywrightToolExecutor(page)
                result_text = await executor.execute("extract_job_links", {})
                elapsed = time.time() - start

                try:
                    links = _json.loads(result_text)
                except (_json.JSONDecodeError, TypeError):
                    links = []

                # Count how many pass strict vs relaxed filter
                from app.services.hot_company_search import (
                    _looks_like_direct_job_url,
                    _looks_like_job_url_relaxed,
                )

                strict = [l for l in links if _looks_like_direct_job_url(l.get("url", ""))]
                relaxed = [
                    l
                    for l in links
                    if _looks_like_job_url_relaxed(l.get("url", ""))
                    and not _looks_like_direct_job_url(l.get("url", ""))
                ]

                print(f"  Total links extracted: {len(links)}  ({elapsed:.1f}s)")
                print(f"  Strict job URLs:       {len(strict)}")
                print(f"  Relaxed job URLs:      {len(relaxed)}")
                print(f"  Other (filtered):      {len(links) - len(strict) - len(relaxed)}")

                # Show a few examples from each bucket
                if strict:
                    print("  Strict samples:")
                    for l in strict[:3]:
                        print(f"    • {l.get('title', '?')[:60]}")
                        print(f"      {l.get('url', '?')[:120]}")
                if relaxed:
                    print("  Relaxed samples:")
                    for l in relaxed[:3]:
                        print(f"    • {l.get('title', '?')[:60]}")
                        print(f"      {l.get('url', '?')[:120]}")
                if not strict and not relaxed and links:
                    print("  Non-job samples (first 3):")
                    for l in links[:3]:
                        print(f"    • {l.get('title', '?')[:60]}")
                        print(f"      {l.get('url', '?')[:120]}")
            finally:
                await page.close()
    finally:
        await context.close()


async def run_perplexity_mode(args: argparse.Namespace) -> None:
    """Test the Perplexity drill in isolation for each company.

    Calls _drill_perplexity_for_job directly and reports whether it
    returned a hit, with timing and the imported job title. No browser
    agent, no full hot-search pipeline — just the Perplexity step.
    """
    from app.services.hot_company_search import _drill_perplexity_for_job

    cases = CASES
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",") if s.strip()}
        cases = [c for c in CASES if any(w in c[0].lower() for w in wanted)]
        if not cases:
            print(f"No cases matched --only={args.only}")
            return

    print(f"\nRunning {len(cases)} Perplexity drill cases...\n")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    results: list[dict] = []
    for i, (name, _url, guidance) in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] === {name} ===")
        print(f"  Guidance: {guidance}")
        start = time.time()
        try:
            hits = await asyncio.wait_for(
                _drill_perplexity_for_job(
                    company_name=name,
                    guidance=guidance,
                    profile_keywords=PROFILE_KEYWORDS,
                    locations=None,
                ),
                timeout=45,
            )
        except TimeoutError:
            hits = None
            error = "TIMEOUT"
        except Exception as e:
            hits = None
            error = f"{type(e).__name__}: {e}"
        else:
            error = None

        elapsed = time.time() - start
        r = {"name": name, "hits": hits or [], "elapsed": elapsed, "error": error}
        results.append(r)

        if error:
            print(f"  ❌ {error}  ({elapsed:.1f}s)")
        elif hits:
            hit = hits[0]
            top = hit.top_jobs[0] if hit.top_jobs else {}
            print(f"  ✅ IMPORTED  ({elapsed:.1f}s)")
            print(f"    → {top.get('title', '?')[:70]}")
            print(f"      {top.get('url', '?')}")
        else:
            print(f"  ⚪ NO_HIT  ({elapsed:.1f}s)")

    # Summary
    print("\n" + "=" * 60)
    print("PERPLEXITY DRILL SUMMARY")
    print("=" * 60)
    imported = sum(1 for r in results if r["hits"])
    no_hit = sum(1 for r in results if not r["hits"] and not r["error"])
    errors = sum(1 for r in results if r["error"])
    total = len(results)
    print(f"  Imported:  {imported}/{total}  ({100 * imported / total:.0f}%)")
    print(f"  No hit:    {no_hit}/{total}")
    print(f"  Errors:    {errors}/{total}")
    print()
    for r in results:
        if r["error"]:
            mark, label = "❌", r["error"][:30]
        elif r["hits"]:
            mark, label = "✅", "IMPORTED"
        else:
            mark, label = "⚪", "NO_HIT"
        print(f"  {mark} {r['name']:<20} {r['elapsed']:>5.1f}s  {label}")
    print()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        type=str,
        help="Comma-separated company names to run (case-insensitive substring match)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show tool-by-tool agent logs and full tracebacks on errors",
    )
    parser.add_argument(
        "--diag",
        action="store_true",
        help="Skip the agent loop — just navigate to the URL, run extract_job_links, and dump what it found",
    )
    parser.add_argument(
        "--perplexity",
        action="store_true",
        help="Test the Perplexity drill in isolation (skip browser agent)",
    )
    args = parser.parse_args()

    if args.diag:
        await run_diag_mode(args)
        return
    if args.perplexity:
        await run_perplexity_mode(args)
        return

    # Wire up logging to stdout so agent events are visible
    log_level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    hc_logger = logging.getLogger("app.services.hot_company_search")
    if args.verbose:
        hc_logger.setLevel(logging.INFO)
    else:
        # Quiet on stdout; capture handler (attached below) still sees INFO
        hc_logger.setLevel(logging.INFO)
        hc_logger.propagate = False  # don't double-print to root
    # Silence httpx chatter
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Attach capture handler for outcome classification
    capture = _CaptureHandler()
    hc_logger.addHandler(capture)

    # Filter cases if --only was passed
    cases = CASES
    if args.only:
        wanted = {s.strip().lower() for s in args.only.split(",") if s.strip()}
        cases = [(n, u, g) for (n, u, g) in CASES if any(w in n.lower() for w in wanted)]
        if not cases:
            print(f"No cases matched --only={args.only}")
            print("Available:", ", ".join(n for (n, _, _) in CASES))
            return

    print(f"\nRunning {len(cases)} browser-agent cases (sequentially, ~30-90s each)...\n")

    results: list[dict] = []
    for i, (name, url, guidance) in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] === {name} ===")
        print(f"  URL:       {url}")
        print(f"  Guidance:  {guidance}")
        r = await run_one(name, url, guidance, capture, verbose=args.verbose)
        results.append(r)
        print(format_result(r))
        if args.verbose and r.get("traceback"):
            print(r["traceback"])
        if args.verbose:
            print(f"  [captured {len(r['logs'].messages)} log messages]")
            for msg in r["logs"].messages[-10:]:
                print(f"    | {msg[:120]}")
        print()

    # Summary table
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total = len(results)
    buckets: dict[str, int] = {}
    for r in results:
        _, label = classify(r)
        buckets[label] = buckets.get(label, 0) + 1

    # Success rate: IMPORTED + AGENT_OK counts as "agent succeeded end-to-end"
    agent_success = buckets.get("IMPORTED", 0) + buckets.get("AGENT_OK (dup in DB)", 0)
    print(f"  Agent-level success:  {agent_success}/{total}  ({100 * agent_success / total:.0f}%)")
    print("    (imported OR agent picked a real job that was already in DB)")
    print()
    for label, count in sorted(buckets.items(), key=lambda x: -x[1]):
        print(f"  {label:<22}  {count}")
    print()
    for r in results:
        emoji, label = classify(r)
        print(
            f"  {emoji} {r['name']:<18}  {r['elapsed']:>5.1f}s  "
            f"{r['strict_links']:>3}s/{r['relaxed_links']:<3}r  {label}"
        )
    print()


if __name__ == "__main__":
    asyncio.run(main())
