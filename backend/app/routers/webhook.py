from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.schemas.webhook import NocDBWebhookPayload
from app.services import webhook_service

router = APIRouter(prefix="/api", tags=["webhook"])


@router.post("/feedback/webhook")
async def nocodb_webhook(
    body: NocDBWebhookPayload, session: AsyncSession = Depends(get_session)
):
    result = await webhook_service.handle_nocodb_webhook(session, body.model_dump())
    return result
