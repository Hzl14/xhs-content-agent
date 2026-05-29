import asyncio

from agents.publisher_agent import PublisherAgent
from api.handlers import run_agent_pipeline
from core.agent_base import BaseAgent
from core.session_manager import SessionManager
from models.evaluation import ReviewCritique
from models.schemas import (
    AgentRunRequest,
    ContentItem,
    DraftPackage,
    GeneratedTopicWithContents,
    NodeTrace,
    PipelinePlan,
    PipelineState,
    PlannedStageItem,
    TopicItem,
)
from models.states import PipelineStage, PlannedStageStatus
from services.session_service import SessionService


class FakeLLMService:
    enabled = False


class FakeMemoryManager:
    async def build_memory_context(self, user_id: str, session_id: str, query: str) -> str:
        return ""


class FakeDraftService:
    def save_pipeline_draft(self, state: PipelineState) -> DraftPackage:
        return DraftPackage(
            draft_id=f"{state.task_id}:{state.run_id}",
            json_path="data/output/drafts/mock/draft.json",
            markdown_path="data/output/drafts/mock/draft.md",
            json_url="/drafts/mock/draft.json",
            markdown_url="/drafts/mock/draft.md",
            content_count=sum(len(block.contents) for block in state.results),
        )


class NoopAgent(BaseAgent):
    name = "noop"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        return state


class PlannerStubAgent(BaseAgent):
    name = "planner_stub"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        state.topics = [TopicItem(title="Mock topic", reason="User asked for a publishable draft.")]
        state.plan = PipelinePlan(
            needs_crawl=False,
            needs_analysis=False,
            needs_topic_generation=False,
            needs_content_generation=True,
            needs_review=True,
            needs_publish=True,
            planned_stages=[
                PlannedStageItem(stage=PipelineStage.CONTENT_GENERATING, status=PlannedStageStatus.READY),
                PlannedStageItem(stage=PipelineStage.REVIEWING, status=PlannedStageStatus.PENDING),
                PlannedStageItem(stage=PipelineStage.PUBLISHING, status=PlannedStageStatus.PENDING),
            ],
        )
        return state


class ContentStubAgent(BaseAgent):
    name = "content_stub"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        state.results = [
            GeneratedTopicWithContents(
                topic=state.topics[0],
                contents=[
                    ContentItem(
                        title="Mock publish draft",
                        body="This is a generated draft that must be confirmed before publishing.",
                        hashtags=["xhs", "agent"],
                        cta="Ready to publish?",
                        image_suggestion="Use one clean product screenshot.",
                    )
                ],
            )
        ]
        return state


class ReviewerStubAgent(BaseAgent):
    name = "reviewer_stub"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        state.results[0].critique = ReviewCritique(
            hard_gate_passed=True,
            total_score=88.0,
            weak_dimensions=[],
        )
        return state


class FakeContainer:
    def __init__(self) -> None:
        self.llm_service = FakeLLMService()
        self.memory_manager = FakeMemoryManager()
        self.draft_service = FakeDraftService()
        self.session_service = SessionService(SessionManager())
        self.planner_agent = PlannerStubAgent()
        self.crawler_agent = NoopAgent()
        self.analysis_agent = NoopAgent()
        self.topic_agent = NoopAgent()
        self.content_agent = ContentStubAgent()
        self.reviewer_agent = ReviewerStubAgent()
        self.publisher_agent = PublisherAgent()


def test_agent_run_defers_publish_then_confirms_mock_publish():
    container = FakeContainer()

    first = asyncio.run(
        run_agent_pipeline(
            container,
            AgentRunRequest(
                user_id="user-1",
                session_id="session-1",
                user_message="Generate a Xiaohongshu post and publish it.",
            ),
        )
    )

    assert first.failed is False
    assert first.stage == PipelineStage.WAITING_FOR_INPUT
    assert first.needs_clarification is True
    assert first.draft_package is not None
    assert "确认" in first.clarification_question

    active_generation = container.session_service.get_active_generation("user-1", "session-1")
    assert active_generation is not None
    assert active_generation["publish_requested"] is True
    assert active_generation["status"] == "awaiting_publish_confirmation"
    active_task = container.session_service.get_active_task("user-1", "session-1")
    assert active_task is not None
    assert active_task["task_type"] == "copywriting"
    assert active_task["candidates"][0]["content_title"] == "Mock publish draft"

    second = asyncio.run(
        run_agent_pipeline(
            container,
            AgentRunRequest(
                user_id="user-1",
                session_id="session-1",
                task_id=first.task_id,
                user_message="发布第1篇",
            ),
        )
    )

    assert second.failed is False
    assert second.stage == PipelineStage.COMPLETED
    assert second.publish_record is not None
    assert second.publish_record.publish_id.startswith("mock_")
    assert container.session_service.get_active_generation("user-1", "session-1") is None
    assert container.session_service.get_active_task("user-1", "session-1") is None
