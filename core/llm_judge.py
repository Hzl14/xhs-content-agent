from __future__ import annotations

from models.evaluation import LLMJudgeResult
from models.prompts import LLM_JUDGE_PROMPT
from models.schemas import AnalysisResult, ContentItem, PipelineState
from services.llm_service import LLMService


class LLMJudge:
    def __init__(self, llm_service: LLMService, min_rule_score: float = 60.0) -> None:
        self.llm_service = llm_service
        self.min_rule_score = min_rule_score

    async def evaluate(
        self,
        content: ContentItem,
        analysis: AnalysisResult | None,
        state: PipelineState | None = None,
    ) -> LLMJudgeResult | None:
        if not self.llm_service.enabled:
            return None

        user = LLM_JUDGE_PROMPT.render_user(
            title=content.title,
            body=content.body,
            cta=content.cta,
            hashtags=", ".join(content.hashtags),
            audience=state.audience if state else "",
            tone=state.tone if state else "",
            content_type=content.content_type,
            analysis_summary=analysis.summary if analysis else "暂无",
        )
        result = await self.llm_service.chat_json(
            system=LLM_JUDGE_PROMPT.system,
            user=user,
        )
        parsed = self.llm_service.extract_json(result.content) or {}
        return LLMJudgeResult(
            authenticity_score=self._normalize_score(parsed.get("authenticity_score")),
            tone_fit_score=self._normalize_score(parsed.get("tone_fit_score")),
            ai_trace_score=self._normalize_score(parsed.get("ai_trace_score")),
            main_issue=str(parsed.get("main_issue") or "").strip(),
            suggestion=str(parsed.get("suggestion") or "").strip(),
        )

    @staticmethod
    def _normalize_score(value) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(score, 100.0))
