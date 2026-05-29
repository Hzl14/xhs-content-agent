from core.evaluation_engine import EvaluationEngine
from core.llm_judge import LLMJudge
from models.evaluation import LLMJudgeResult, ReviewCritique
from models.schemas import AnalysisResult, ContentItem, PipelineState
from services.llm_service import LLMService


class EvaluationService:
    def __init__(self, llm_service: LLMService | None = None) -> None:
        self.engine = EvaluationEngine()
        self.llm_judge = LLMJudge(llm_service) if llm_service else None

    async def review(
        self,
        content: ContentItem,
        analysis: AnalysisResult | None,
        state: PipelineState | None = None,
    ) -> ReviewCritique:
        critique = self.engine.evaluate(content=content, analysis=analysis)

        if (
            self.llm_judge is None
            or not critique.hard_gate_passed
            or critique.total_score < self.llm_judge.min_rule_score
        ):
            return critique

        try:
            judge_result = await self.llm_judge.evaluate(
                content=content,
                analysis=analysis,
                state=state,
            )
        except Exception:  # noqa: BLE001
            return critique

        if judge_result is None:
            return critique

        self._merge_llm_judge(critique, judge_result)
        return critique

    def _merge_llm_judge(self, critique: ReviewCritique, judge: LLMJudgeResult) -> None:
        critique.llm_authenticity_score = judge.authenticity_score
        critique.llm_tone_fit_score = judge.tone_fit_score
        critique.llm_ai_trace_score = judge.ai_trace_score
        critique.llm_main_issue = judge.main_issue

        llm_average = (
            judge.authenticity_score
            + judge.tone_fit_score
            + judge.ai_trace_score
        ) / 3
        critique.total_score = round(critique.total_score * 0.7 + llm_average * 0.3, 2)

        if judge.authenticity_score < 75 and "authenticity_score" not in critique.weak_dimensions:
            critique.weak_dimensions.append("authenticity_score")
            critique.issues.append("LLM 判官认为真实感不足")
            critique.pattern_feedback.dimension_rules[
                "authenticity_score"
            ] = "正文必须像真实用户分享，加入具体经历、判断依据和不完美但自然的表达。"
            critique.pattern_feedback.failed_patterns.append("内容读起来缺少真实用户经历和具体判断依据。")
        if judge.tone_fit_score < 75:
            critique.weak_dimensions.append("llm_tone_fit_score")
            critique.issues.append("LLM 判官认为小红书语气适配不足")
            critique.pattern_feedback.dimension_rules[
                "llm_tone_fit_score"
            ] = "语气必须口语化、有分享感，像在和朋友说话，禁止报告式或广告式表达。"
            critique.pattern_feedback.failed_patterns.append("语气不够像小红书真实分享，偏报告或广告表达。")
        if judge.ai_trace_score < 75:
            critique.weak_dimensions.append("llm_ai_trace_score")
            critique.issues.append("LLM 判官认为 AI 模板感偏强")
            critique.pattern_feedback.dimension_rules[
                "llm_ai_trace_score"
            ] = "正文必须减少模板套话和过度工整段落，加入具体场景、个人判断和自然转折。"
            critique.pattern_feedback.failed_patterns.append("内容 AI 模板感偏强，表达过度工整或泛泛而谈。")

        if judge.authenticity_score >= 85:
            critique.pattern_feedback.successful_patterns.append("内容读起来有真实经历和具体判断依据。")
        if judge.tone_fit_score >= 85:
            critique.pattern_feedback.successful_patterns.append("语气口语化、有分享感，贴近小红书表达。")
        if judge.ai_trace_score >= 85:
            critique.pattern_feedback.successful_patterns.append("表达自然，AI 模板感较弱。")

        if judge.main_issue and judge.main_issue != "无":
            critique.issues.append(f"[语感问题] {judge.main_issue}")
        if judge.suggestion and judge.suggestion != "无":
            critique.suggestions.append(f"[LLM建议] {judge.suggestion}")
