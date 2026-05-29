from models.states import PipelineStage


VALID_TRANSITIONS: dict[PipelineStage, set[PipelineStage]] = {
    PipelineStage.IDLE: {
        PipelineStage.WAITING_FOR_INPUT,
        PipelineStage.CRAWLING,
        PipelineStage.ANALYZING,
        PipelineStage.TOPIC_GENERATING,
        PipelineStage.CONTENT_GENERATING,
        PipelineStage.REVIEWING,
        PipelineStage.PUBLISHING,
        PipelineStage.FAILED,
    },
    PipelineStage.WAITING_FOR_INPUT: set(),
    PipelineStage.CRAWLING: {
        PipelineStage.ANALYZING,
        PipelineStage.TOPIC_GENERATING,
        PipelineStage.CONTENT_GENERATING,
        PipelineStage.REVIEWING,
        PipelineStage.PUBLISHING,
        PipelineStage.FAILED,
    },
    PipelineStage.ANALYZING: {
        PipelineStage.TOPIC_GENERATING,
        PipelineStage.CONTENT_GENERATING,
        PipelineStage.REVIEWING,
        PipelineStage.PUBLISHING,
        PipelineStage.FAILED,
    },
    PipelineStage.TOPIC_GENERATING: {
        PipelineStage.CONTENT_GENERATING,
        PipelineStage.REVIEWING,
        PipelineStage.PUBLISHING,
        PipelineStage.FAILED,
    },
    PipelineStage.CONTENT_GENERATING: {PipelineStage.REVIEWING, PipelineStage.PUBLISHING, PipelineStage.FAILED},
    PipelineStage.REVIEWING: {
        PipelineStage.CONTENT_GENERATING,
        PipelineStage.PUBLISHING,
        PipelineStage.FAILED,
    },
    PipelineStage.PUBLISHING: {PipelineStage.COMPLETED, PipelineStage.FAILED},
    PipelineStage.COMPLETED: set(),
    PipelineStage.FAILED: set(),
}


class PipelineStateMachine:
    @staticmethod
    def can_transition(from_stage: PipelineStage, to_stage: PipelineStage) -> bool:
        return to_stage in VALID_TRANSITIONS.get(from_stage, set())
