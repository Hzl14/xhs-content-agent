from __future__ import annotations

from memory.models import SessionState


class MemoryPromptBuilder:
    """
    将记忆状态组装进 LLM prompt。
    关注点完全分离：此类只管格式，不管存储和检索。
    """

    def build(
        self,
        state: SessionState,
        query: str,
        cross_summaries: list[dict],
        cross_turn_hits: list[dict],
    ) -> str:
        parts: list[str] = []

        # ── 当前 session 历史摘要 ──────────────────────────────────────────
        if state.summary and state.summary.current_summary:
            parts.append("[当前会话历史摘要]")
            parts.append(state.summary.current_summary)

        # ── 待压缩区（只展示 turn_summary，不展示原文）────────────────────
        if state.pending_summary_turns:
            parts.append("\n[当前会话近期概要]")
            for turn in state.pending_summary_turns:
                parts.append(f"- {turn.turn_summary}")

        # ── 跨 session 摘要（中等相关）────────────────────────────────────
        if cross_summaries:
            parts.append("\n[用户历史会话摘要（相关）]")
            for item in cross_summaries:
                score_pct = int(item["score"] * 100)
                parts.append(f"## 会话 {item['session_id'][:8]}… 相关度 {score_pct}%")
                parts.append(item["summary_text"])

        # ── 跨 session 原文（强相关）──────────────────────────────────────
        if cross_turn_hits:
            parts.append("\n[用户历史会话原文（高度相关）]")
            for hit in cross_turn_hits:
                score_pct = int(hit["score"] * 100)
                parts.append(f"## 会话 {hit['session_id'][:8]}… 相关度 {score_pct}%")
                for turn in hit["turns"]:
                    parts.append(f"用户：{turn.user_message.content}")
                    if turn.assistant_message:
                        parts.append(f"助手：{turn.assistant_message.content}")

        # ── 当前 session 最近 N 轮原文 ────────────────────────────────────
        recent = state.active_turns[-7:]
        if recent:
            parts.append("\n[当前会话最近对话]")
            for turn in recent:
                parts.append(f"用户：{turn.user_message.content}")
                if turn.assistant_message:
                    parts.append(f"助手：{turn.assistant_message.content}")

        # ── 当前问题 ──────────────────────────────────────────────────────
        parts.append("\n[当前问题]")
        parts.append(query)

        return "\n".join(parts)
