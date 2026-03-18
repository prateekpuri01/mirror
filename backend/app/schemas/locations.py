import uuid

from pydantic import BaseModel, ConfigDict


class LocationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    city: str
    state: str | None = None
    country: str
    is_remote: bool
