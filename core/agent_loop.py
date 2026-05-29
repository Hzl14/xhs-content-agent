from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.agent_base import BaseAgent
from core.state_machine import PipelineStateMachine
from models.schemas import PipelineState
from models.states import PipelineStage, PlannedStageStatus


SaveStateFn = Callable[[PipelineState], None]


@dataclass
class LoopHooks:
    """
    Default loop policy.

    AgentLoop owns orchestration; agents only read/write PipelineState.
    Hooks keep routing decisions explicit and testable without leaking
    per-agent implementation details into the engine.
    """

    max_outer_rounds: int = 12
    max_stage_retries: int = 1
    max_content_regenerations: int = 0

    def before_stage(self, state: PipelineState, stage: PipelineStage) -> PipelineStage:
        return stage

    def should_retry_stage(
        self,
        state: PipelineState,
        stage: PipelineStage,
        attempt: int,
    ) -> bool:
        if attempt >= self.max_stage_retries:
            return False
        return self._last_error_is_retryable(state)

    def should_skip_failed_stage(self, state: PipelineState, stage: PipelineStage) -> bool:
        return stage == PipelineStage.PUBLISHING

    def after_stage(self, state: PipelineState, stage: PipelineStage) -> None:
        if stage == PipelineStage.REVIEWING:
            self._route_failed_review_back_to_content(state)

    def should_continue_outer_loop(self, state: PipelineState, outer_round: int) -> bool:
        if outer_round >= self.max_outer_rounds:
            state.failed = True
            state.error_message = f"Agent loop exceeded max_outer_rounds={self.max_outer_rounds}"
            return False
        return False

    @staticmethod
    def _last_error_is_retryable(state: PipelineState) -> bool:
        message = (state.error_message or "").lower()
        retryable_markers = [
            "timeout",
            "timed out",
            "rate limit",
            "temporarily",
            "connection",
            "json",
            "empty",
        ]
        return any(marker in message for marker in retryable_markers)

    def _route_failed_review_back_to_content(self, state: PipelineState) -> None:
        if not state.results:
            return
        failed_blocks = [
            block for block in state.results
            if block.critique is not None and not block.critique.passed
        ]
        if not failed_blocks:
            state.metadata["review_decision"] = {
                "action": "pass",
                "reason": "all reviewed content passed threshold",
            }
            return

        regenerations = int(state.metadata.get("content_regeneration_count", 0))
        if regenerations >= self.max_content_regenerations:
            state.metadata["review_decision"] = {
                "action": "use_best_effort",
                "reason": "review did not pass after max content regenerations; returning best available draft",
                "failed_topics": [block.topic.title for block in failed_blocks],
                "weak_dimensions": [
                    dimension
                    for block in failed_blocks
                    for dimension in (block.critique.weak_dimensions if block.critique else [])
                ],
            }
            return

        if not _has_stage(state, PipelineStage.CONTENT_GENERATING):
            state.metadata["review_decision"] = {
                "action": "fail",
                "reason": "review failed but content generation is not in planned stages",
                "failed_topics": [block.topic.title for block in failed_blocks],
            }
            state.failed = True
            state.error_message = "Review failed and content regeneration is unavailable."
            return

        state.metadata["content_regeneration_count"] = regenerations + 1
        state.metadata["review_decision"] = {
            "action": "regenerate_content",
            "reason": "review score below threshold after reviewer reflection",
            "failed_topics": [block.topic.title for block in failed_blocks],
            "weak_dimensions": [
                dimension
                for block in failed_blocks
                for dimension in (block.critique.weak_dimensions if block.critique else [])
            ],
        }
        _mark_stage_ready(state, PipelineStage.CONTENT_GENERATING)
        _mark_stage_pending(state, PipelineStage.REVIEWING)


class AgentLoopEngine:
    """
    Two-layer loop engine:
    - outer loop: follow-up/continue rounds
    - inner loop: stage-by-stage execution
    """

    def __init__(
        self,
        stage_agents: dict[PipelineStage, BaseAgent],
        save_state: SaveStateFn,
        stage_plan: list[PipelineStage] | None = None,
    ) -> None:
        self.stage_agents = stage_agents
        self.save_state = save_state
        self.stage_plan = stage_plan or [
            PipelineStage.CRAWLING,
            PipelineStage.ANALYZING,
            PipelineStage.TOPIC_GENERATING,
            PipelineStage.CONTENT_GENERATING,
            PipelineStage.REVIEWING,
            PipelineStage.PUBLISHING,
        ]

    async def run(self, state: PipelineState, hooks: LoopHooks | None = None) -> PipelineState:
        hooks = hooks or LoopHooks()
        outer_round = 0

        while True:
            outer_round += 1
            state.metadata["outer_round"] = outer_round
            if outer_round > hooks.max_outer_rounds:
                state.failed = True
                state.error_message = f"Agent loop exceeded max_outer_rounds={hooks.max_outer_rounds}"
                state.stage = PipelineStage.FAILED
                self.save_state(state)
                return state

            state.failed = False
            state.error_message = None

            ready_stages = self._resolve_stage_plan(state)
            if not ready_stages and self._has_remaining_planned_stages(state):
                state.failed = True
                state.error_message = "No ready stage found while planned stages remain."
                break

            for stage in ready_stages:
                if state.failed:
                    break

                target_stage = hooks.before_stage(state, stage)
                if not PipelineStateMachine.can_transition(state.stage, target_stage):
                    state.failed = True
                    state.error_message = f"Invalid transition: {state.stage} -> {target_stage}"
                    break

                state.stage = target_stage
                self.save_state(state)

                agent = self.stage_agents.get(target_stage)
                if agent is None:
                    state.failed = True
                    state.error_message = f"No agent found for stage: {target_stage}"
                    break

                validation_error = self._validate_stage_inputs(state, target_stage)
                if validation_error:
                    state.failed = True
                    state.error_message = validation_error
                    break

                attempt = 1
                while True:
                    state = await agent.run(state)
                    if not state.failed:
                        validation_error = self._validate_stage_outputs(state, target_stage)
                        if validation_error:
                            state.failed = True
                            state.error_message = validation_error
                            if state.traces:
                                state.traces[-1].status = "failed"
                                state.traces[-1].error = validation_error
                    self.save_state(state)

                    if not state.failed:
                        break
                    if not hooks.should_retry_stage(state, target_stage, attempt):
                        break

                    # retry path
                    state.failed = False
                    state.error_message = None
                    attempt += 1

                if state.failed:
                    if hooks.should_skip_failed_stage(state, target_stage):
                        _mark_stage_skipped(state, target_stage)
                        _advance_after_skipped_stage(state, target_stage)
                        state.failed = False
                        state.error_message = None
                        self.save_state(state)
                        continue
                    _mark_stage_failed(state, target_stage)
                    break

                hooks.after_stage(state, target_stage)
                if state.failed:
                    break

                if self._stage_has_status(state, target_stage, PlannedStageStatus.PENDING):
                    self.save_state(state)
                    break

                self._advance_planned_stage(state, target_stage)
                self.save_state(state)

            if state.failed:
                state.stage = PipelineStage.FAILED
                self.save_state(state)
                return state

            if self._has_remaining_planned_stages(state):
                continue

            if hooks.should_continue_outer_loop(state, outer_round):
                self.save_state(state)
                if state.failed:
                    state.stage = PipelineStage.FAILED
                    self.save_state(state)
                    return state
                continue

            state.stage = PipelineStage.COMPLETED
            self.save_state(state)
            return state

    def _resolve_stage_plan(self, state: PipelineState) -> list[PipelineStage]:
        if state.plan.planned_stages:
            return [item.stage for item in state.plan.planned_stages if item.status == PlannedStageStatus.READY]
        return self.stage_plan

    @staticmethod
    def _has_remaining_planned_stages(state: PipelineState) -> bool:
        if not state.plan.planned_stages:
            return False
        return any(
            item.status in {PlannedStageStatus.READY, PlannedStageStatus.PENDING}
            for item in state.plan.planned_stages
        )

    @staticmethod
    def _stage_has_status(
        state: PipelineState,
        stage: PipelineStage,
        status: PlannedStageStatus,
    ) -> bool:
        if not state.plan.planned_stages:
            return False
        return any(item.stage == stage and item.status == status for item in state.plan.planned_stages)

    @staticmethod
    def _validate_stage_inputs(state: PipelineState, stage: PipelineStage) -> str | None:
        if stage == PipelineStage.CRAWLING:
            if not state.candidate_notes and not (state.search_keywords or state.search_query):
                return "CrawlerAgent requires search_query/search_keywords or candidate_notes in PipelineState."
        elif stage == PipelineStage.ANALYZING:
            if not state.input_notes and not state.candidate_notes:
                return "AnalysisAgent requires input_notes or candidate_notes in PipelineState."
        elif stage == PipelineStage.TOPIC_GENERATING:
            if state.analysis is None and state.mode != "fast":
                return "TopicAgent requires analysis in PipelineState."
        elif stage == PipelineStage.CONTENT_GENERATING:
            if not state.topics:
                return "ContentAgent requires topics in PipelineState."
        elif stage == PipelineStage.REVIEWING:
            if not state.results:
                return "ReviewerAgent requires generated results in PipelineState."
        return None

    @staticmethod
    def _validate_stage_outputs(state: PipelineState, stage: PipelineStage) -> str | None:
        if stage == PipelineStage.CRAWLING:
            if not state.input_notes:
                return "CrawlerAgent did not produce input_notes."
        elif stage == PipelineStage.ANALYZING:
            if state.analysis is None:
                return "AnalysisAgent did not produce analysis."
        elif stage == PipelineStage.TOPIC_GENERATING:
            if not state.topics:
                return "TopicAgent did not produce topics."
        elif stage == PipelineStage.CONTENT_GENERATING:
            if not state.results:
                return "ContentAgent did not produce results."
            empty_topics = [block.topic.title for block in state.results if not block.contents]
            if empty_topics:
                return f"ContentAgent produced empty contents for topics: {', '.join(empty_topics)}."
        elif stage == PipelineStage.REVIEWING:
            if not state.results:
                return "ReviewerAgent requires generated results in PipelineState."
            missing_topics = [block.topic.title for block in state.results if block.critique is None]
            if missing_topics:
                return f"ReviewerAgent did not produce critique for topics: {', '.join(missing_topics)}."
        return None

    @staticmethod
    def _advance_planned_stage(state: PipelineState, completed_stage: PipelineStage) -> None:
        if not state.plan.planned_stages:
            return

        next_ready_index: int | None = None
        for index, item in enumerate(state.plan.planned_stages):
            if item.stage != completed_stage:
                continue
            item.status = PlannedStageStatus.DONE
            next_ready_index = index + 1
            break

        if next_ready_index is None:
            return

        for item in state.plan.planned_stages[next_ready_index:]:
            if item.status == PlannedStageStatus.PENDING:
                item.status = PlannedStageStatus.READY
                break


def _has_stage(state: PipelineState, stage: PipelineStage) -> bool:
    return any(item.stage == stage for item in state.plan.planned_stages)


def _mark_stage_ready(state: PipelineState, stage: PipelineStage) -> None:
    for item in state.plan.planned_stages:
        if item.stage == stage:
            item.status = PlannedStageStatus.READY


def _mark_stage_pending(state: PipelineState, stage: PipelineStage) -> None:
    for item in state.plan.planned_stages:
        if item.stage == stage:
            item.status = PlannedStageStatus.PENDING


def _mark_stage_skipped(state: PipelineState, stage: PipelineStage) -> None:
    for item in state.plan.planned_stages:
        if item.stage == stage:
            item.status = PlannedStageStatus.SKIPPED


def _advance_after_skipped_stage(state: PipelineState, skipped_stage: PipelineStage) -> None:
    next_ready_index: int | None = None
    for index, item in enumerate(state.plan.planned_stages):
        if item.stage == skipped_stage:
            next_ready_index = index + 1
            break

    if next_ready_index is None:
        return

    for item in state.plan.planned_stages[next_ready_index:]:
        if item.status == PlannedStageStatus.PENDING:
            item.status = PlannedStageStatus.READY
            break


def _mark_stage_failed(state: PipelineState, stage: PipelineStage) -> None:
    for item in state.plan.planned_stages:
        if item.stage == stage:
            item.status = PlannedStageStatus.FAILED
