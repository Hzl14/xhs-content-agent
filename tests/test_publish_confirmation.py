import asyncio

from agents.publisher_agent import PublisherAgent
from api.handlers import _defer_publish_until_user_confirmation, _run_publish_confirmation
from models.schemas import (
    ContentItem,
    GeneratedTopicWithContents,
    PipelinePlan,
    PipelineState,
    PlannedStageItem,
    TopicItem,
)
from models.states import PipelineStage, PlannedStageStatus


class FakeSessionService:
    def __init__(self) -> None:
        self.saved = []
        self.cleared_user_id = None

    def save(self, state: PipelineState) -> None:
        self.saved.append(state)

    def clear_active_generation(self, user_id: str, session_id: str) -> None:
        self.cleared_user_id = user_id
        self.cleared_session_id = session_id


class FakeContainer:
    def __init__(self) -> None:
        self.publisher_agent = PublisherAgent()
        self.session_service = FakeSessionService()


def test_publish_stage_is_deferred_until_user_confirmation():
    state = PipelineState(
        run_id="publish-plan",
        plan=PipelinePlan(
            needs_publish=True,
            planned_stages=[
                PlannedStageItem(stage=PipelineStage.CONTENT_GENERATING, status=PlannedStageStatus.DONE),
                PlannedStageItem(stage=PipelineStage.REVIEWING, status=PlannedStageStatus.DONE),
                PlannedStageItem(stage=PipelineStage.PUBLISHING, status=PlannedStageStatus.READY),
            ],
        ),
    )

    _defer_publish_until_user_confirmation(state)

    assert state.metadata["publish_requested"] is True
    assert state.metadata["publish_confirmation_required"] is True
    assert state.plan.needs_publish is False
    assert state.plan.planned_stages[-1].status == PlannedStageStatus.SKIPPED


def test_confirm_publish_runs_publisher_and_clears_active_generation():
    container = FakeContainer()
    state = PipelineState(
        run_id="confirm-publish",
        session_id="session-1",
        task_id="task-1",
        user_id="user-1",
        user_message="发布第1篇",
    )
    content = ContentItem(
        title="3个细节让文案更像真人分享",
        body="这是一段已经通过审核、等待用户确认后发布的文案正文。",
        hashtags=["文案", "小红书", "真实分享"],
        cta="你觉得这版可以发吗?",
    )
    active_generation = {
        "parent_run_id": "parent-run",
        "publish_requested": True,
        "status": "awaiting_publish_confirmation",
        "selected_index": None,
        "candidates": [
            {
                "index": 1,
                "topic_title": "小红书文案优化",
                "topic_reason": "用户要求生成并发布。",
                "content_title": content.title,
                "content": content.model_dump(),
                "critique": None,
                "score": 88,
            }
        ],
    }

    response = asyncio.run(_run_publish_confirmation(container, state, active_generation))

    assert response.failed is False
    assert response.stage == PipelineStage.COMPLETED
    assert response.results[0].contents[0].title == content.title
    assert container.session_service.cleared_user_id == "user-1"
    assert container.session_service.cleared_session_id == "session-1"
    assert container.session_service.saved[-1].metadata["publish_confirmed"] is True
    assert response.publish_record is not None
    assert response.publish_record.publish_id.startswith("mock_")
    assert container.session_service.saved[-1].metadata["publish_status"] == "simulated_success"
