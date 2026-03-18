import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def handle_nocodb_webhook(session: AsyncSession, payload: dict) -> dict:
    """Process a NocoDB webhook event. Stub for now — logs and returns."""
    event_type = payload.get("type", "unknown")
    logger.info("NocoDB webhook received: type=%s", event_type)
    # Future: parse row changes, update thumbs/status, trigger AI feedback loop
    return {"status": "received", "type": event_type}
