import asyncio

from core.agent_base import BaseAgent
from core.agent_loop import AgentLoopEngine, LoopHooks
from models.evaluation import ReviewCritique
from models.schemas import (
    AnalysisResult,
    ContentItem,
    GeneratedTopicWithContents,
    NodeTrace,
    PipelinePlan,
    PipelineState,
    PlannedStageItem,
    TopicItem,
)
from models.states import PipelineStage, PlannedStageStatus


class ContentStubAgent(BaseAgent):
    name = "content_stub"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        count = int(state.metadata.get("content_runs", 0)) + 1
        state.metadata["content_runs"] = count
        state.results = [
            GeneratedTopicWithContents(
                topic=state.topics[0],
                contents=[
                    ContentItem(
                        title=f"generated title {count}",
                        body="generated body",
                        hashtags=["tag1", "tag2", "tag3"],
                        cta="comment?",
                    )
                ],
            )
        ]
        return state


class EmptyContentAgent(BaseAgent):
    name = "empty_content"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        return state


class ReviewStubAgent(BaseAgent):
    name = "review_stub"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        count = int(state.metadata.get("review_runs", 0)) + 1
        state.metadata["review_runs"] = count
        score = 50.0 if count == 1 else 90.0
        state.results[0].critique = ReviewCritique(
            hard_gate_passed=True,
            total_score=score,
            weak_dimensions=[] if score >= 75 else ["hook_score"],
        )
        return state


class AlwaysFailReviewAgent(BaseAgent):
    name = "always_fail_review"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        state.results[0].critique = ReviewCritique(
            hard_gate_passed=True,
            total_score=50.0,
            weak_dimensions=["hook_score"],
        )
        return state


class MissingCritiqueReviewAgent(BaseAgent):
    name = "missing_critique_review"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        return state


def _state() -> PipelineState:
    return PipelineState(
        run_id="loop-test",
        analysis=AnalysisResult(summary="test analysis"),
        topics=[TopicItem(title="test topic", reason="test reason")],
        plan=PipelinePlan(
            planned_stages=[
                PlannedStageItem(stage=PipelineStage.CONTENT_GENERATING, status=PlannedStageStatus.READY),
                PlannedStageItem(stage=PipelineStage.REVIEWING, status=PlannedStageStatus.PENDING),
            ],
            needs_content_generation=True,
            needs_review=True,
        ),
    )


def test_agent_loop_routes_failed_review_back_to_content():
    saved_stages: list[PipelineStage] = []

    def save(state: PipelineState) -> None:
        saved_stages.append(state.stage)

    engine = AgentLoopEngine(
        stage_agents={
            PipelineStage.CONTENT_GENERATING: ContentStubAgent(),
            PipelineStage.REVIEWING: ReviewStubAgent(),
        },
        save_state=save,
    )

    result = asyncio.run(engine.run(_state(), hooks=LoopHooks(max_content_regenerations=2)))

    assert not result.failed
    assert result.stage == PipelineStage.COMPLETED
    assert result.metadata["content_runs"] == 2
    assert result.metadata["review_runs"] == 2
    assert result.metadata["review_decision"]["action"] == "pass"
    assert result.results[0].critique is not None
    assert result.results[0].critique.passed
    assert PipelineStage.CONTENT_GENERATING in saved_stages
    assert PipelineStage.REVIEWING in saved_stages


def test_agent_loop_returns_best_effort_when_review_regeneration_limit_is_reached():
    engine = AgentLoopEngine(
        stage_agents={
            PipelineStage.CONTENT_GENERATING: ContentStubAgent(),
            PipelineStage.REVIEWING: AlwaysFailReviewAgent(),
        },
        save_state=lambda state: None,
    )

    result = asyncio.run(engine.run(_state(), hooks=LoopHooks(max_content_regenerations=1)))

    assert not result.failed
    assert result.stage == PipelineStage.COMPLETED
    assert result.metadata["content_regeneration_count"] == 1
    assert result.metadata["review_decision"]["action"] == "use_best_effort"
    assert result.results[0].critique is not None
    assert not result.results[0].critique.passed


def test_agent_loop_fails_when_stage_does_not_produce_required_output():
    engine = AgentLoopEngine(
        stage_agents={
            PipelineStage.CONTENT_GENERATING: EmptyContentAgent(),
        },
        save_state=lambda state: None,
    )

    result = asyncio.run(engine.run(_state()))

    assert result.failed
    assert result.stage == PipelineStage.FAILED
    assert result.error_message == "ContentAgent did not produce results."
    assert result.traces[-1].status == "failed"
    assert result.traces[-1].error == "ContentAgent did not produce results."


def test_agent_loop_fails_when_reviewer_does_not_produce_critique():
    state = _state()
    state.results = [
        GeneratedTopicWithContents(
            topic=state.topics[0],
            contents=[
                ContentItem(
                    title="generated title",
                    body="generated body",
                    hashtags=["tag1", "tag2", "tag3"],
                    cta="comment?",
                )
            ],
        )
    ]
    state.plan.planned_stages = [
        PlannedStageItem(stage=PipelineStage.REVIEWING, status=PlannedStageStatus.READY),
    ]
    engine = AgentLoopEngine(
        stage_agents={
            PipelineStage.REVIEWING: MissingCritiqueReviewAgent(),
        },
        save_state=lambda state: None,
    )

    result = asyncio.run(engine.run(state))

    assert result.failed
    assert result.stage == PipelineStage.FAILED
    assert result.error_message == "ReviewerAgent did not produce critique for topics: test topic."
    assert result.traces[-1].status == "failed"
