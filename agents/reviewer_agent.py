from __future__ import annotations

from typing import TYPE_CHECKING

from core.agent_base import BaseAgent
from models.prompts import REVIEW_REWRITE_PROMPT, SHARED_CONTEXT_HEADER
from core.reflection_engine import ReflectionEngine
from core.text_postprocess import clean_content_item
from models.schemas import ContentItem, NodeTrace, PipelineState
from services.evaluation_service import EvaluationService
from services.llm_service import LLMService

if TYPE_CHECKING:
    from memory.manager import ConversationManager


class ReviewerAgent(BaseAgent):
    name = "reviewer"

    def __init__(
        self,
        evaluation_service: EvaluationService,
        llm_service: LLMService,
        memory_manager: ConversationManager | None = None,
    ) -> None:
        self.evaluation_service = evaluation_service
        self.llm_service = llm_service
        # memory_manager 可选注入；None 时跳过记忆写回
        self.memory_manager = memory_manager

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        engine = ReflectionEngine(
            threshold=state.review_threshold,
            max_reflections=state.max_reflections,
        )

        for block in state.results:
            if not block.contents:
                continue

            latest = block.contents[-1]
            critique = await self._score_content(latest, state)
            attempt = 1
            while self._should_retry_reflection(engine, critique.total_score, attempt):
                trace.retry_count += 1
                latest = await self._rewrite(latest, critique, state)
                block.contents[-1] = latest
                critique = await self._score_content(latest, state)
                attempt += 1

            block.critique = critique

        scores = [r.critique.total_score for r in state.results if r.critique]
        trace.review_score = round(sum(scores) / len(scores), 2) if scores else None
        trace.status = "success"

        # ── 写回会话记忆 ──────────────────────────────────────────────────────
        # 把「用户本次请求」和「本次 pipeline 最终生成结果」作为一轮对话存入记忆，
        # 供下次请求的 TopicAgent / ContentAgent 参考历史偏好。
        if self.memory_manager is not None:
            self._persist_pattern_feedback(state)
            await self._persist_to_memory(state)

        return state

    def _persist_pattern_feedback(self, state: PipelineState) -> None:
        if self.memory_manager is None:
            return
        critiques = [block.critique for block in state.results if block.critique]
        if not critiques:
            return
        try:
            self.memory_manager.record_pattern_feedback(
                user_id=state.user_id,
                critiques=critiques,
            )
        except Exception:  # noqa: BLE001
            pass

    async def _persist_to_memory(self, state: PipelineState) -> None:
        """将本次 pipeline 的输入意图和输出结果写入会话记忆。"""
        try:
            user_content = state.user_message.strip() or self._build_user_message(state)
            assistant_content = self._build_assistant_message(state)
            state.ai_message = assistant_content

            user_msg = self.memory_manager.add_user_message(
                user_id=state.user_id,
                session_id=state.session_id,
                content=user_content,
            )
            assistant_msg = self.memory_manager.add_assistant_message(
                user_id=state.user_id,
                session_id=state.session_id,
                content=assistant_content,
            )
            await self.memory_manager.finalize_turn(
                user_id=state.user_id,
                session_id=state.session_id,
                user_msg=user_msg,
                assistant_msg=assistant_msg,
            )
        except Exception:  # noqa: BLE001
            # 记忆写入失败不影响主流程
            pass

    @staticmethod
    def _build_user_message(state: PipelineState) -> str:
        analysis_summary = state.analysis.summary if state.analysis else ""
        return (
            f"请基于当前任务生成小红书内容。受众：{state.audience}；语气：{state.tone}。\n"
            f"分析摘要：{analysis_summary}"
        ).strip()

    @staticmethod
    def _build_assistant_message(state: PipelineState) -> str:
        result_lines: list[str] = []
        for block in state.results:
            topic_title = block.topic.title
            content_titles = [c.title for c in block.contents]
            score = block.critique.total_score if block.critique else None
            if score is None:
                result_lines.append(f"选题《{topic_title}》→ 内容：{', '.join(content_titles)}")
            else:
                result_lines.append(
                    f"选题《{topic_title}》→ 内容：{', '.join(content_titles)}；评分：{score}"
                )
        return "本次生成结果：\n" + "\n".join(result_lines)

    # TODO(USER_DESIGN): customize score dimensions/weights here.
    async def _score_content(self, content: ContentItem, state: PipelineState):
        return await self.evaluation_service.review(
            content=content,
            analysis=state.analysis,
            state=state,
        )

    # TODO(USER_DESIGN): customize reflection retry policy.
    def _should_retry_reflection(self, engine: ReflectionEngine, score: float, attempt: int) -> bool:
        return engine.should_retry(score=score, attempt=attempt)

    async def _rewrite(self, content: ContentItem, critique, state: PipelineState) -> ContentItem:
        if not self.llm_service.enabled:
            return self._fallback_rewrite(content)

        shared_context = (
            f"{SHARED_CONTEXT_HEADER}\n{state.llm_context}" if state.llm_context else SHARED_CONTEXT_HEADER
        )
        system = REVIEW_REWRITE_PROMPT.system
        user = REVIEW_REWRITE_PROMPT.render_user(
            shared_context=shared_context,
            title=content.title,
            body=content.body,
            issues="; ".join(critique.issues) or "无",
            suggestions="; ".join(critique.suggestions) or "请增强吸引力、完整性和互动性",
        )
        user = (
            f"{user}\n\n"
            "重写硬约束：禁止使用 Markdown 语法（**、##、*、[]()、```）。"
            "如果用户没有提供真实素材，不要编造第一人称亲身经历、具体年龄、天数或结果。"
            "优先修正扣分问题，不要把观点分析类内容改写成个人故事。"
        )
        result = await self.llm_service.chat_json(system=system, user=user)
        parsed = self.llm_service.extract_json(result.content) or {}
        item = parsed.get("content")
        if not isinstance(item, dict):
            return self._fallback_rewrite(content)
        try:
            return clean_content_item(ContentItem(**item))
        except Exception:  # noqa: BLE001
            return self._fallback_rewrite(content)

    @staticmethod
    def _fallback_rewrite(content: ContentItem) -> ContentItem:
        title = content.title if any(ch.isdigit() for ch in content.title) else f"3个重点｜{content.title}"
        body = content.body
        if "✨" not in body:
            body += "\n\n✨ 重点先收藏，按步骤执行会更稳。"
        cta = content.cta if content.cta.strip().endswith("？") else f"{content.cta.rstrip('。')}？"
        hashtags = content.hashtags if len(content.hashtags) >= 2 else content.hashtags + ["干货", "避坑"]
        return clean_content_item(ContentItem(
            title=title,
            body=body,
            hashtags=hashtags,
            cta=cta,
            image_suggestion=content.image_suggestion,
            content_type=content.content_type,
        ))
