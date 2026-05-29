from enum import Enum


class PipelineStage(str, Enum):
    IDLE = "IDLE"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    CRAWLING = "CRAWLING"
    ANALYZING = "ANALYZING"
    TOPIC_GENERATING = "TOPIC_GENERATING"
    CONTENT_GENERATING = "CONTENT_GENERATING"
    REVIEWING = "REVIEWING"
    PUBLISHING = "PUBLISHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PlannedStageStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
