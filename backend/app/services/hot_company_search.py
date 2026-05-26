"""Hot Company Search — public surface for the v2 pipeline.

Implementation lives in ``app.services.hot_search.*``:
  - ``types``           — CompanyCandidate, CompanyHit, SearchEvent
  - ``llm_helpers``     — shared OpenAI/JSON helpers
  - ``discovery``       — aggregator-harvest helpers + ATS slug probing
                          + careers-URL discovery (still used by v2)
  - ``discovery_v2``    — LLM-web-first company discovery (Phase A2)
  - ``ranking``         — embeddings + cosine top-K + LLM rerank
  - ``discovery_cache`` — discovered_companies persistence + recall
  - ``careers_titles``  — title-only Playwright scraper (Phase D fallback)
  - ``evaluation``      — Phase B/F helpers (dedup, verify, filters,
                          tracked-company DB path, hit summary)
  - ``orchestration_v2``— the SSE-streaming run_hot_company_search_v2
                          single-pass orchestrator

Historical callers (tests, eval scripts) imported helpers by name from
this module. We continue to re-export the public surface for backwards
compatibility.
"""

# Public types
# Star-import every public-named symbol from each layer that still has
# external callers.
from app.services.hot_search.discovery import *  # noqa: F401, F403

# Explicit re-exports for the underscore-prefixed names that external
# callers (tests, scripts) historically imported. ``from … import *``
# wouldn't pick these up, so we name them here.
from app.services.hot_search.discovery import (  # noqa: F401
    _QUERY_USER_TEMPLATE,
    _looks_like_direct_job_url,
    _looks_like_job_url_relaxed,
)
from app.services.hot_search.evaluation import *  # noqa: F401, F403
from app.services.hot_search.evaluation import (  # noqa: F401
    _expand_location,
    _job_passes_location_filter,
    _job_passes_salary_filter,
)

# Orchestration entry point — v2 is the only pipeline. The v1 dispatcher
# was removed when v1's drill cascade was deleted; eval scripts and the
# router both call this name.
from app.services.hot_search.orchestration_v2 import (  # noqa: F401
    run_hot_company_search_v2 as run_hot_company_search,
)
from app.services.hot_search.types import (  # noqa: F401
    CompanyCandidate,
    CompanyHit,
    SearchEvent,
)
