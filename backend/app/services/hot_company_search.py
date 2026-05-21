"""Hot Company Search — backwards-compat re-export shim.

The actual implementation now lives in ``app.services.hot_search.*``:
  - ``types``        — CompanyCandidate, CompanyHit, SearchEvent
  - ``llm_helpers``  — shared OpenAI/JSON helpers used by both layers
  - ``discovery``    — query gen, web search, candidate extraction, slug
                       harvesting + probing, careers-URL discovery
  - ``evaluation``   — picker, verifier, drill strategies, location/salary
                       filters, per-candidate evaluation, dedup
  - ``orchestration``— the SSE-streaming run_hot_company_search() loop

External callers (router, tests, eval scripts) historically import private
helpers from this module by name. To keep those working without forcing a
sweeping import refactor, this file re-exports the public surface.
New code should import from ``app.services.hot_search.*`` directly.
"""

# Public types
from app.services.hot_search.types import (  # noqa: F401
    CompanyCandidate,
    CompanyHit,
    SearchEvent,
)

# Star-import every public-named symbol from each layer.
from app.services.hot_search.discovery import *  # noqa: F401, F403
from app.services.hot_search.evaluation import *  # noqa: F401, F403

# Explicit re-exports for the underscore-prefixed names that external
# callers (tests, scripts) historically imported. `from … import *`
# wouldn't pick these up, so we name them here. Keeps the shim a single
# source of "what we promise the world about the old import path."
from app.services.hot_search.discovery import (  # noqa: F401
    _QUERY_USER_TEMPLATE,
    _looks_like_direct_job_url,
    _looks_like_job_url_relaxed,
)
from app.services.hot_search.evaluation import (  # noqa: F401
    _crawl_careers_page_for_job,
    _drill_perplexity_for_job,
    _expand_location,
    _job_passes_location_filter,
    _job_passes_salary_filter,
)

# Orchestration entry point — what the router and eval scripts call.
# v1 lives at `orchestration.run_hot_company_search`; v2 at
# `orchestration_v2.run_hot_company_search_v2`. The dispatcher below
# picks one based on the runtime flag so the router signature is stable.
from app.services.hot_search.orchestration import (  # noqa: F401
    run_hot_company_search as _run_v1,
)
from app.services.hot_search.orchestration_v2 import (  # noqa: F401
    run_hot_company_search_v2 as _run_v2,
)


def run_hot_company_search(*args, **kwargs):
    """Dispatch to v1 or v2 based on ``settings.hot_search_v2``.

    Returns the same ``AsyncGenerator[SearchEvent, None]`` either way,
    so callers (router, eval scripts) need no change. The flag is read
    on each call so config can be flipped without process restart.
    """
    from app.config import settings
    if settings.hot_search_v2:
        return _run_v2(*args, **kwargs)
    return _run_v1(*args, **kwargs)
