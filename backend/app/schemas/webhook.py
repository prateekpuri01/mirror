from pydantic import BaseModel


class NocDBWebhookPayload(BaseModel):
    type: str
    data: dict
