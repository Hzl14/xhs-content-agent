from __future__ import annotations

import json
from typing import TYPE_CHECKING

from memory.models import Turn
from memory.summarizer import LLMSummarizer

if TYPE_CHECKING:
    from memory.storage import FileStorage
    from services.llm_service import LLMService

# 中文停用词，用于关键词过滤
_STOP_WORDS = {
    "用户", "助手", "询问", "回应", "表示", "说", "的", "了", "是", "在",
    "和", "与", "或", "但", "也", "都", "这", "那", "有", "没有",
    "可以", "会", "要", "不", "很", "一个", "什么", "如何", "为什么",
}


class CrossSessionRetriever:
    """
    跨 session 检索器。
    优先使用 LLM embedding 做语义相似度；
    LLM 不可用时降级为 TF-IDF char n-gram；
    sklearn 也不可用时再降级为词集合 Jaccard。
    """

    def __init__(
        self,
        storage: FileStorage,
        llm_service: LLMService,
        strong_threshold: float = 0.75,
        mid_threshold: float = 0.50,
    ) -> None:
        self.storage = storage
        self.llm_service = llm_service
        self.strong_threshold = strong_threshold
        self.mid_threshold = mid_threshold

    async def retrieve(
        self,
        user_id: str,
        current_session_id: str,
        query: str,
        max_cross_full_turns: int = 3,
    ) -> tuple[list[dict], list[dict]]:
        """
        返回 (summary_hits, turn_hits)
        summary_hits：跨 session 摘要中和 query 相关的（中等相关度）
        turn_hits：跨 session 原文对话中和 query 强相关的
        """
        sessions = self.storage.load_user_sessions(user_id)
        summary_hits: list[dict] = []
        strong_turn_hits: list[dict] = []

        for session_meta in sessions:
            session_id = session_meta["session_id"]
            if session_id == current_session_id:
                continue

            state = self.storage.load_state(user_id, session_id)

            # ── 摘要层检索（中等阈值）────────────────────────────────────────
            if state.summary and state.summary.current_summary:
                target = state.summary.current_summary + " " + " ".join(
                    w for w in state.summary.keywords if w not in _STOP_WORDS
                )
                score = await self._score(query, target)
                if score >= self.mid_threshold:
                    summary_hits.append({
                        "session_id": session_id,
                        "score": score,
                        "summary_text": state.summary.current_summary,
                        "keywords": state.summary.keywords,
                    })

            # ── 原文 Turn 层检索（强阈值）────────────────────────────────────
            turn_candidates = state.active_turns + state.pending_summary_turns
            if not turn_candidates:
                continue

            scored: list[tuple[float, Turn]] = []
            for t in turn_candidates:
                s = await self._score(query, t.retrieval_text())
                scored.append((s, t))
            scored.sort(key=lambda x: x[0], reverse=True)

            selected = [t for s, t in scored if s >= self.strong_threshold][:max_cross_full_turns]
            if selected:
                strong_turn_hits.append({
                    "session_id": session_id,
                    "score": scored[0][0],
                    "turns": selected,
                })

        summary_hits.sort(key=lambda x: x["score"], reverse=True)
        strong_turn_hits.sort(key=lambda x: x["score"], reverse=True)

        # 跨 session 完整对话限制总预算
        limited_turn_hits: list[dict] = []
        remaining = max_cross_full_turns
        for hit in strong_turn_hits:
            if remaining <= 0:
                break
            turns = hit["turns"][:remaining]
            if turns:
                limited_turn_hits.append({**hit, "turns": turns})
                remaining -= len(turns)

        return summary_hits[:3], limited_turn_hits

    # ── 相似度计算（三级降级）────────────────────────────────────────────────

    async def _score(self, query: str, text: str) -> float:
        if not text.strip():
            return 0.0

        # Cross-session retrieval is on the hot path of every request.
        # Do not call chat models once per historical turn here; it makes
        # normal agent runs block for minutes when history grows.
        val = self._tfidf_score(query, text)
        if val is not None:
            return val

        # 兜底：词集合 Jaccard
        return self._jaccard_score(query, text)

    async def _embedding_score(self, query: str, text: str) -> float | None:
        """
        调用 LLM 的 embedding 接口计算余弦相似度。
        使用 chat 接口模拟（实际部署可换真正的 embedding API）。
        """
        try:
            system = (
                "判断以下两段文本的语义相关度，输出 0~1 之间的小数，只输出数字，不要解释。\n"
                "1=完全相关，0=完全无关。"
            )
            user = f"文本A：{query[:200]}\n文本B：{text[:400]}"
            result = await self.llm_service.chat(system=system, user=user, temperature=0.0)
            content = result.content.strip()
            # 提取第一个数字
            import re
            m = re.search(r"[01](?:\.\d+)?|\d\.\d+", content)
            if m:
                val = float(m.group())
                return min(1.0, max(0.0, val))
            return None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _tfidf_score(query: str, text: str) -> float | None:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1)
            matrix = vec.fit_transform([query, text])
            return float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _jaccard_score(query: str, text: str) -> float:
        q = set(query.lower().split()) - _STOP_WORDS
        t = set(text.lower().split()) - _STOP_WORDS
        if not q or not t:
            return 0.0
        return len(q & t) / len(q | t)
