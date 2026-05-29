from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from memory.models import Turn, SessionSummary

if TYPE_CHECKING:
    from services.llm_service import LLMService

# 停用词：这些词频繁出现但对检索毫无价值
_STOP_WORDS = {
    "用户", "助手", "询问", "回应", "表示", "说", "的", "了", "是", "在",
    "和", "与", "或", "但", "也", "都", "这", "那", "有", "没有",
    "可以", "会", "要", "不", "很", "一个", "什么", "如何", "为什么",
}


class LLMSummarizer:
    """
    使用 LLM 生成真正的语义摘要。
    降级方案：LLM 不可用时用规则截断，但会明确标注。
    """

    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def summarize_turn(self, turn: Turn) -> tuple[str, list[str]]:
        """为单轮对话生成摘要和关键词，优先调用 LLM。"""
        user_text = turn.user_message.content.strip()
        assistant_text = (
            turn.assistant_message.content.strip() if turn.assistant_message else ""
        )

        if self.llm_service.enabled:
            summary, keywords = await self._llm_summarize_turn(user_text, assistant_text)
        else:
            summary, keywords = self._rule_summarize_turn(user_text, assistant_text)

        return summary, keywords

    async def summarize_session_incremental(
        self,
        session_id: str,
        old_summary: SessionSummary | None,
        pending_turns: list[Turn],
    ) -> SessionSummary:
        """增量更新会话摘要，把 pending_turns 压缩合并进旧摘要。"""
        # 确保每轮都有摘要
        for turn in pending_turns:
            if not turn.turn_summary:
                turn.turn_summary, turn.turn_keywords = await self.summarize_turn(turn)

        pending_text = "\n".join(f"- {t.turn_summary}" for t in pending_turns)
        merged_keywords = []
        for t in pending_turns:
            merged_keywords.extend(t.turn_keywords)
        covered_turn_ids = [t.turn_id for t in pending_turns]

        if self.llm_service.enabled:
            current_summary = await self._llm_merge_summary(
                old_summary.current_summary if old_summary else "",
                pending_text,
            )
        else:
            # 降级：简单拼接
            old_text = old_summary.current_summary.strip() if old_summary else ""
            block = f"[新增]\n{pending_text}"
            current_summary = f"{old_text}\n\n{block}".strip() if old_text else block

        if old_summary:
            # 关键词去重合并，限制总量
            all_kw = list(dict.fromkeys(old_summary.keywords + merged_keywords))[:50]
            covered = old_summary.covered_turn_ids + covered_turn_ids
            version = old_summary.summary_version + 1
            previous_summary = old_summary.current_summary
        else:
            all_kw = list(dict.fromkeys(merged_keywords))[:50]
            covered = covered_turn_ids
            version = 1
            previous_summary = ""

        return SessionSummary(
            session_id=session_id,
            summary_version=version,
            previous_summary=previous_summary,
            current_summary=current_summary,
            keywords=all_kw,
            covered_turn_ids=covered,
            updated_at=time.time(),
        )

    # ── 私有方法 ──────────────────────────────────────────────────────────────

    async def _llm_summarize_turn(
        self, user_text: str, assistant_text: str
    ) -> tuple[str, list[str]]:
        system = (
            "你是对话摘要助手。提取本轮对话的核心信息，输出 JSON。\n"
            '格式：{"summary": "50字以内摘要", "keywords": ["关键词1", "关键词2", ...]}\n'
            "关键词提取规则：只保留有实际意义的名词/动词，最多8个，排除常用虚词。"
        )
        user = f"用户：{user_text[:500]}\n助手：{assistant_text[:800]}"
        result = await self.llm_service.chat_json(system=system, user=user)
        parsed = self.llm_service.extract_json(result.content) or {}

        summary = parsed.get("summary", "")
        keywords = parsed.get("keywords", [])

        # 解析失败降级
        if not summary:
            summary, keywords = self._rule_summarize_turn(user_text, assistant_text)

        return str(summary), [str(k) for k in keywords if k]

    async def _llm_merge_summary(self, old_summary: str, pending_text: str) -> str:
        if not pending_text.strip():
            return old_summary

        system = (
            "你是会话历史压缩助手。将旧摘要和新增对话合并为一段连贯的摘要。\n"
            "要求：保留关键偏好/决策/数据，去除冗余，控制在200字以内。只输出摘要文本，不要JSON。"
        )
        old_part = f"[旧摘要]\n{old_summary}\n\n" if old_summary else ""
        user = f"{old_part}[新增对话]\n{pending_text}"
        result = await self.llm_service.chat(system=system, user=user)
        merged = result.content.strip()
        return merged if merged else (old_summary + "\n" + pending_text).strip()

    @staticmethod
    def _rule_summarize_turn(user_text: str, assistant_text: str) -> tuple[str, list[str]]:
        """LLM 不可用时的规则降级，截断并标注。"""
        u = user_text.replace("\n", " ")[:100]
        a = assistant_text.replace("\n", " ")[:150] if assistant_text else "（无回复）"
        summary = f"用户：{u}；助手：{a}"
        keywords = LLMSummarizer.extract_keywords(user_text + " " + assistant_text)
        return summary, keywords

    @staticmethod
    def extract_keywords(text: str) -> list[str]:
        """从文本中提取有效关键词，过滤停用词。"""
        words: list[str] = []
        for token in text.replace("\n", " ").replace("，", " ").replace("。", " ").split():
            token = token.strip(',:;.!?()[]{}"\'"')
            if len(token) >= 2 and token not in _STOP_WORDS:
                words.append(token)
        # 去重保序
        seen: list[str] = []
        for w in words:
            if w not in seen:
                seen.append(w)
        return seen[:12]
