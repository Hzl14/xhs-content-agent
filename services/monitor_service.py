from models.schemas import PipelineState


class MonitorService:
    @staticmethod
    def summarize(state: PipelineState) -> dict:
        total_tokens = sum(t.input_tokens + t.output_tokens for t in state.traces)
        total_latency_ms = round(sum(t.latency_ms or 0 for t in state.traces), 2)
        return {
            "run_id": state.run_id,
            "stage": state.stage,
            "failed": state.failed,
            "total_tokens": total_tokens,
            "total_latency_ms": total_latency_ms,
            "node_count": len(state.traces),
        }

