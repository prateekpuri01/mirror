import uuid

from pydantic import BaseModel, ConfigDict


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    color: str | None = None
