"""Hot Company Search — SSE endpoint for AI-powered company discovery."""

import json
import logging

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.schemas.companies import HotSearchRequest
from app.services.hot_company_search import run_hot_company_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/companies", tags=["hot-search"])


@router.post("/hot-search")
async def hot_search(body: HotSearchRequest) -> StreamingResponse:
    """Stream company discovery results as SSE events."""

    async def event_stream():
        try:
            async for event in run_hot_company_search(
                sources=body.sources,
                guidance=body.guidance,
                max_hits=body.max_hits,
                max_iterations=body.max_iterations,
                locations=body.locations,
                min_salary=body.min_salary,
                reference_job_ids=body.reference_job_ids,
                candidate_concurrency=body.candidate_concurrency,
                profile_fit_threshold=body.profile_fit_threshold,
                effort=body.effort,
            ):
                yield f"event: {event.event}\ndata: {json.dumps(event.data)}\n\n"
        except Exception:
            logger.exception("Hot search stream error")
            error_data = json.dumps({"message": "Internal search error"})
            yield f"event: error\ndata: {error_data}\n\n"
            done_data = json.dumps({"total_hits": 0, "total_candidates_checked": 0})
            yield f"event: done\ndata: {done_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
