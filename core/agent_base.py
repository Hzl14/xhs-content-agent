from __future__ import annotations

import time
from abc import ABC, abstractmethod

from models.schemas import NodeTrace, PipelineState
from services.trace_service import begin_span


class BaseAgent(ABC):
    name: str = "base"

    async def run(self, state: PipelineState) -> PipelineState:
        original_state = state
        trace = NodeTrace(node_name=self.name, status="running")
        span = begin_span(
            "node",
            self.name,
            input_summary={
                "stage": str(state.stage),
                "task_id": state.task_id,
                "current_node": state.current_node,
            },
            as_parent=True,
        )
        started = time.perf_counter()
        state.current_node = self.name
        try:
            result = await self._execute(state, trace)
            if result is None:
                raise RuntimeError(f"{self.name} returned None instead of PipelineState")
            state = result
            if trace.status == "running":
                trace.status = "success"
            return state
        except Exception as exc:  # noqa: BLE001
            state = state if isinstance(state, PipelineState) else original_state
            state.failed = True
            state.error_message = str(exc)
            trace.status = "failed"
            trace.error = str(exc)
            return state
        finally:
            trace.latency_ms = round((time.perf_counter() - started) * 1000, 2)
            state = state if isinstance(state, PipelineState) else original_state
            self._enrich_trace(state, trace)
            state.traces.append(trace)
            span.end(
                status=trace.status,
                output_summary={
                    "stage": str(state.stage),
                    "failed": state.failed,
                    "input_tokens": trace.input_tokens,
                    "output_tokens": trace.output_tokens,
                    "retry_count": trace.retry_count,
                    "review_score": trace.review_score,
                },
                error=trace.error,
            )

    @abstractmethod
    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        raise NotImplementedError

    # TODO(USER_DESIGN): override in specific agents to record custom trace metrics.
    def _enrich_trace(self, state: PipelineState, trace: NodeTrace) -> None:
        return None
