from enum import Enum
from pydantic import BaseModel, Field
from datetime import datetime


class EventType(str, Enum):
    PIPELINE_STARTED = "pipeline_started"
    NODE_STARTED = "node_started"
    NODE_COMPLETED = "node_completed"
    NODE_FAILED = "node_failed"
    PIPELINE_COMPLETED = "pipeline_completed"


class AgentEvent(BaseModel):
    event_type: EventType
    run_id: str
    node_name: str | None = None
    message: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)

