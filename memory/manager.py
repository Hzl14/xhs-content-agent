from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from memory.config import MemoryConfig
from memory.models import Message, Turn
from models.evaluation import ReviewCritique
from memory.pattern_feedback import PatternFeedbackStore
from memory.storage import FileStorage
from memory.summarizer import LLMSummarizer
from memory.retriever import CrossSessionRetriever
from memory.prompt_builder import MemoryPromptBuilder
from memory.tokenizer import estimate_tokens
from services.trace_service import begin_span

if TYPE_CHECKING:
    from services.llm_service import LLMService


class ConversationManager:
    """
    会话记忆管理器，负责：
    1. 记录每轮 user/assistant 消息
    2. 到阈值时触发 LLM 摘要压缩
    3. 构建携带记忆的 prompt
    4. 跨 session 语义检索历史偏好
    """

    def __init__(
        self,
        llm_service: LLMService,
        config: MemoryConfig | None = None,
    ) -> None:
        self.config = config or MemoryConfig()
        self.storage = FileStorage(base_dir=self.config.memory_base_dir)
        self.summarizer = LLMSummarizer(llm_service)
        self.retriever = CrossSessionRetriever(
            storage=self.storage,
            llm_service=llm_service,
            strong_threshold=self.config.strong_related_threshold,
            mid_threshold=self.config.mid_related_threshold,
        )
        self.prompt_builder = MemoryPromptBuilder()
        self.pattern_feedback_store = PatternFeedbackStore(
            base_dir=self.config.memory_base_dir,
            max_active_rules=self.config.pattern_feedback_max_active_rules,
            max_patterns=self.config.pattern_feedback_max_patterns,
            resolve_after_successes=self.config.pattern_feedback_resolve_after_successes,
            compact_every_updates=self.config.pattern_feedback_compact_every_updates,
            success_threshold=self.config.pattern_feedback_success_threshold,
        )

    # ── 消息写入 ──────────────────────────────────────────────────────────────

    def add_user_message(
        self, user_id: str, session_id: str, content: str
    ) -> Message:
        msg = Message(
            message_id=str(uuid.uuid4()),
            role="user",
            content=content,
            timestamp=time.time(),
            token_count=estimate_tokens(content),
        )
        self.storage.append_raw_message(user_id, session_id, msg)
        return msg

    def add_assistant_message(
        self, user_id: str, session_id: str, content: str
    ) -> Message:
        msg = Message(
            message_id=str(uuid.uuid4()),
            role="assistant",
            content=content,
            timestamp=time.time(),
            token_count=estimate_tokens(content),
        )
        self.storage.append_raw_message(user_id, session_id, msg)
        return msg

    # ── Prompt 构建 ───────────────────────────────────────────────────────────

    async def build_memory_context(
        self, user_id: str, session_id: str, query: str
    ) -> str:
        """
        为当前 query 构建携带历史记忆的上下文字符串，
        供各 Agent 拼接进 system/user prompt 使用。
        """
        span = begin_span(
            "memory_read",
            "build_memory_context",
            input_summary={
                "user_id": user_id,
                "session_id": session_id,
                "query_chars": len(query),
            },
        )
        try:
            state = self.storage.load_state(user_id, session_id)
            cross_summaries, cross_turn_hits = await self.retriever.retrieve(
                user_id=user_id,
                current_session_id=session_id,
                query=query,
                max_cross_full_turns=self.config.cross_session_full_turn_budget,
            )
            context = self.prompt_builder.build(state, query, cross_summaries, cross_turn_hits)
            pattern_context = self.pattern_feedback_store.build_prompt_context(user_id)
            final_context = f"{context}\n\n{pattern_context}" if pattern_context else context
            span.end(
                output_summary={
                    "active_turns": len(state.active_turns),
                    "pending_turns": len(state.pending_summary_turns),
                    "has_summary": bool(state.summary),
                    "cross_summary_hits": len(cross_summaries),
                    "cross_turn_hits": sum(len(item.get("turns", [])) for item in cross_turn_hits),
                    "has_pattern_context": bool(pattern_context),
                    "context_chars": len(final_context),
                }
            )
            return final_context
        except Exception as exc:
            span.end(status="failed", error=str(exc))
            raise

    def record_pattern_feedback(self, user_id: str, critiques: list[ReviewCritique]) -> None:
        span = begin_span(
            "memory_write",
            "record_pattern_feedback",
            input_summary={"user_id": user_id, "critique_count": len(critiques)},
        )
        try:
            self.pattern_feedback_store.update_from_critiques(user_id=user_id, critiques=critiques)
            span.end(output_summary={"status": "saved"})
        except Exception as exc:
            span.end(status="failed", error=str(exc))
            raise

    def record_user_revision_feedback(self, user_id: str, feedback: str) -> None:
        span = begin_span(
            "memory_write",
            "record_user_revision_feedback",
            input_summary={"user_id": user_id, "feedback_chars": len(feedback)},
        )
        try:
            self.pattern_feedback_store.update_from_user_feedback(user_id=user_id, feedback=feedback)
            span.end(output_summary={"status": "saved"})
        except Exception as exc:
            span.end(status="failed", error=str(exc))
            raise

    # ── Turn 写回 + 触发压缩 ──────────────────────────────────────────────────

    async def finalize_turn(
        self,
        user_id: str,
        session_id: str,
        user_msg: Message,
        assistant_msg: Message,
    ) -> None:
        """
        pipeline 结束后调用：
        - 把一轮对话写入 active_turns
        - 超出窗口的 turn 移入 pending_summary_turns
        - 达到压缩阈值时调用 LLM 生成正式摘要
        - 持久化状态并更新 user_index
        """
        span = begin_span(
            "memory_write",
            "finalize_turn",
            input_summary={
                "user_id": user_id,
                "session_id": session_id,
                "user_message_chars": len(user_msg.content),
                "assistant_message_chars": len(assistant_msg.content),
            },
        )
        state = self.storage.load_state(user_id, session_id)

        try:
            turn = Turn(
                turn_id=str(uuid.uuid4()),
                user_message=user_msg,
                assistant_message=assistant_msg,
                timestamp=time.time(),
            )
            # 生成摘要和关键词（异步，使用 LLM）
            turn.turn_summary, turn.turn_keywords = await self.summarizer.summarize_turn(turn)

            state.active_turns.append(turn)
            state.updated_at = time.time()
            state.total_active_tokens = self._recompute_active_tokens(state)

            # 超出短期窗口：最老的 turn 移到 pending 区
            while len(state.active_turns) > self.config.short_term_active_turns:
                moved = state.active_turns.pop(0)
                state.pending_summary_turns.append(moved)

            compacted = False
            # 检查是否需要正式压缩
            if self._should_compact(state):
                await self._compact(state)
                compacted = True

            state.total_active_tokens = self._recompute_active_tokens(state)
            self.storage.save_state(state)
            if state.summary:
                self.storage.save_summary(user_id, session_id, state.summary)
            self.storage.update_user_index(
                user_id=user_id,
                session_id=session_id,
                summary_keywords=state.summary.keywords if state.summary else [],
                updated_at=state.updated_at,
            )
            span.end(
                output_summary={
                    "turn_id": turn.turn_id,
                    "active_turns": len(state.active_turns),
                    "pending_turns": len(state.pending_summary_turns),
                    "compacted": compacted,
                    "summary_version": state.summary.summary_version if state.summary else None,
                    "turn_keywords": turn.turn_keywords,
                }
            )
        except Exception as exc:
            span.end(status="failed", error=str(exc))
            raise

    # ── 前端展示（与记忆逻辑解耦）────────────────────────────────────────────

    def get_frontend_recent_turns(self, user_id: str, session_id: str) -> list[dict]:
        return self.storage.load_recent_raw_messages(
            user_id=user_id,
            session_id=session_id,
            max_messages=self.config.frontend_display_turns * 2,
        )

    # ── 私有方法 ──────────────────────────────────────────────────────────────

    def _should_compact(self, state) -> bool:
        if state.effective_turn_count >= self.config.formal_summary_trigger_turns:
            return True
        if self._estimate_context_tokens(state) >= self.config.hard_token_limit:
            return True
        return False

    async def _compact(self, state) -> None:
        if not state.pending_summary_turns:
            return
        new_summary = await self.summarizer.summarize_session_incremental(
            session_id=state.session_id,
            old_summary=state.summary,
            pending_turns=state.pending_summary_turns,
        )
        state.summary = new_summary
        self.storage.append_archive_turns(
            state.user_id, state.session_id, state.pending_summary_turns
        )
        state.pending_summary_turns = []
        state.updated_at = time.time()

    def _recompute_active_tokens(self, state) -> int:
        total = 0
        for turn in state.active_turns:
            total += turn.user_message.token_count
            if turn.assistant_message:
                total += turn.assistant_message.token_count
        return total

    def _estimate_context_tokens(self, state) -> int:
        tokens = self._recompute_active_tokens(state)
        for turn in state.pending_summary_turns:
            tokens += estimate_tokens(turn.turn_summary)
        if state.summary and state.summary.current_summary:
            tokens += estimate_tokens(state.summary.current_summary)
        return tokens
