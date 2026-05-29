from core.agent_base import BaseAgent
from models.prompts import SHARED_CONTEXT_HEADER, TOPIC_PROMPT
from models.schemas import NodeTrace, PipelineState, TopicItem
from services.llm_service import LLMService


class TopicAgent(BaseAgent):
    name = "topic_generator"

    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        analysis = state.analysis

        if not self.llm_service.enabled:
            state.topics = self._fallback_topics(state)
            trace.status = "success"
            return state

        shared_context = (
            f"{SHARED_CONTEXT_HEADER}\n{state.llm_context}" if state.llm_context else SHARED_CONTEXT_HEADER
        )
        user = TOPIC_PROMPT.render_user(
            shared_context=shared_context,
            analysis_summary=(
                analysis.summary
                if analysis
                else "No trend analysis was run; generate topics from the user request and shared context."
            ),
            top_keywords=", ".join(analysis.top_keywords if analysis else state.search_keywords),
            top_tags=", ".join(analysis.top_tags if analysis else []),
            topic_count=state.topic_count,
        )

        try:
            result = await self.llm_service.chat_json(system=TOPIC_PROMPT.system, user=user)
        except Exception as exc:  # noqa: BLE001
            state.metadata["topic_llm_fallback_reason"] = str(exc)
            state.topics = self._fallback_topics(state)
            trace.status = "success"
            return state
        trace.input_tokens = result.input_tokens
        trace.output_tokens = result.output_tokens

        parsed = self.llm_service.extract_json(result.content) or {}
        topics: list[TopicItem] = []
        for item in parsed.get("topics", [])[: state.topic_count]:
            try:
                topics.append(TopicItem(**item))
            except Exception:  # noqa: BLE001
                continue

        if not topics:
            topics = self._fallback_topics(state)
        state.topics = topics
        trace.status = "success"
        return state

    @staticmethod
    def _fallback_topics(state: PipelineState) -> list[TopicItem]:
        analysis = state.analysis
        if analysis and analysis.top_keywords:
            seeds = analysis.top_keywords[: state.topic_count]
        else:
            seed = state.plan.topic_seed or state.search_query or state.user_message or "XHS content"
            seeds = [seed[:30]]
        return [
            TopicItem(
                title=f"{kw} content angle",
                reason=f"Generate a concrete Xiaohongshu post around {kw}.",
            )
            for kw in seeds[: state.topic_count]
        ]
