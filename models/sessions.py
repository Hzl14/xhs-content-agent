from datetime import datetime
from pydantic import BaseModel, Field


class SessionRecord(BaseModel):
    run_id: str
    session_id: str = "default"
    task_id: str = ""
    status: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
