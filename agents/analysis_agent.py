from collections import Counter
import re
from typing import Sequence

from core.agent_base import BaseAgent
from models.schemas import (
    AnalysisResult,
    ContentInsights,
    EngagementSignals,
    NodeTrace,
    PipelineState,
    StructuralPatterns,
    WritingStrategy,
)
from utils.text_processor import top_keywords


class AnalysisAgent(BaseAgent):
    name = "analysis"
    recommendation_words = ["推荐", "测评", "清单", "教程", "避雷", "分享", "攻略", "合集"]
    pain_words = ["焦虑", "踩坑", "避雷", "不会", "不知道", "后悔", "痛点", "问题", "失败"]
    credibility_words = ["亲测", "实测", "真实", "经验", "数据", "对比", "复盘", "总结"]
    emotion_words = ["焦虑", "惊喜", "治愈", "后悔", "轻松", "快乐", "崩溃", "自信"]
    question_marks = ("?", "？")
    emoji_pattern = re.compile(r"[\U0001F300-\U0001FAFF]")

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        notes = state.input_notes
        if not notes:
            raise ValueError("No notes found for analysis.")

        titles = [note.title for note in notes]
        bodies = [note.content for note in notes]
        keywords = top_keywords(titles + bodies, k=10)
        top_tags = self._top_tags(notes)
        title_patterns = self._title_patterns(titles)

        avg_score = sum(self._engagement_score(note) for note in notes) / len(notes)
        has_number_titles = sum(1 for title in titles if re.search(r"\d", title))
        emoji_notes = sum(1 for text in titles + bodies if self.emoji_pattern.search(text))
        question_endings = sum(1 for title in titles if title.strip().endswith(self.question_marks))
        avg_title_length = round(sum(len(title) for title in titles) / len(titles))
        avg_paragraph_count = round(
            sum(self._paragraph_count(body) for body in bodies) / len(bodies)
        )

        best_note = max(notes, key=self._engagement_score)
        best_note_total = best_note.likes + best_note.favorites + best_note.comments
        best_collect_ratio = self._safe_ratio(best_note.favorites, best_note_total)
        avg_collect_ratio = sum(
            self._safe_ratio(note.favorites, note.likes + note.favorites + note.comments)
            for note in notes
        ) / len(notes)
        avg_comment_ratio = sum(
            self._safe_ratio(note.comments, note.likes + note.favorites + note.comments)
            for note in notes
        ) / len(notes)

        all_text = titles + bodies
        content_value_type = self._infer_content_value_type(notes)
        dominant_narrative = self._infer_dominant_narrative(all_text)
        core_user_pain = self._first_matched_signal(all_text, self.pain_words)
        credibility_signals = self._matched_signals(all_text, self.credibility_words)
        emotional_arc = self._infer_emotional_arc(all_text)
        reusable_expressions = self._reusable_expressions(titles)

        insights = [
            f"高频关键词：{', '.join(keywords[:5]) or '暂无'}",
            f"高频标签：{', '.join(top_tags[:5]) or '暂无'}",
            f"标题数字化比例：{has_number_titles}/{len(notes)}",
            f"样本平均爆款分：{avg_score:.2f}",
            f"内容价值类型：{content_value_type}",
        ]
        summary = (
            f"共分析 {len(notes)} 条内容，当前更受欢迎的表达偏向"
            f"{'、'.join(title_patterns[:3]) if title_patterns else '推荐/测评类'}，"
            f"核心关键词集中在 {', '.join(keywords[:5]) or '暂无明显关键词'}。"
        )

        structural_patterns = StructuralPatterns(
            title_patterns=title_patterns,
            top_keywords=keywords,
            top_tags=top_tags,
            hook_words=title_patterns[:5],
            avg_title_length=avg_title_length,
            avg_paragraph_count=avg_paragraph_count,
            uses_numbering=has_number_titles > 0,
            uses_emoji=emoji_notes > 0,
            ends_with_question=question_endings > 0,
        )
        content_insights = ContentInsights(
            dominant_narrative=dominant_narrative,
            core_user_pain=core_user_pain,
            credibility_signals=credibility_signals,
            emotional_arc=emotional_arc,
            reusable_expressions=reusable_expressions,
            insight_points=insights,
        )
        engagement_signals = EngagementSignals(
            content_value_type=content_value_type,
            avg_collect_ratio=round(avg_collect_ratio, 4),
            avg_comment_ratio=round(avg_comment_ratio, 4),
            best_post_title=best_note.title,
            best_post_collect_ratio=round(best_collect_ratio, 4),
            best_post_key_features=self._best_post_features(best_note),
        )
        writing_strategy = WritingStrategy(
            recommended_title_formula=self._title_formula(title_patterns, keywords),
            opening_strategy=self._opening_strategy(core_user_pain, content_value_type),
            body_structure=self._body_structure(content_value_type, credibility_signals),
            credibility_tactics=self._credibility_tactics(credibility_signals),
            emotional_design={
                "entry": core_user_pain or "从用户的具体困惑切入",
                "turning_point": "给出亲测或对比后的判断",
                "ending": "用可执行建议降低决策成本",
            },
            closing_cta="用问题式结尾引导评论，邀请读者补充自己的经验。",
            tag_strategy={
                "primary_tags": top_tags[:5],
                "keyword_tags": keywords[:5],
            },
            avoid_patterns=["空泛种草", "只堆关键词不解释原因", "标题承诺和正文信息不匹配"],
            must_include_elements=keywords[:3] + credibility_signals[:2],
        )

        state.analysis = AnalysisResult(
            summary=summary,
            sample_size=len(notes),
            structural_patterns=structural_patterns,
            content_insights=content_insights,
            engagement_signals=engagement_signals,
            writing_strategy=writing_strategy,
            top_keywords=keywords,
            top_tags=top_tags,
            title_patterns=title_patterns,
            insight_points=insights,
        )
        trace.status = "success"
        return state

    def _top_tags(self, notes: Sequence) -> list[str]:
        all_tags: list[str] = []
        for note in notes:
            all_tags.extend(note.tags or [])
        return [tag for tag, _ in Counter(all_tags).most_common(10)]

    def _title_patterns(self, titles: Sequence[str]) -> list[str]:
        pattern_counter = Counter()
        for title in titles:
            for word in self.recommendation_words:
                if word in title:
                    pattern_counter[word] += 1
        return [pattern for pattern, _ in pattern_counter.most_common(10)]

    def _engagement_score(self, note) -> float:
        return note.likes * 0.4 + note.favorites * 0.4 + note.comments * 0.2

    def _paragraph_count(self, text: str) -> int:
        paragraphs = [p for p in re.split(r"\n+", text.strip()) if p.strip()]
        return max(len(paragraphs), 1 if text.strip() else 0)

    def _safe_ratio(self, value: int, total: int) -> float:
        return value / total if total > 0 else 0.0

    def _matched_signals(self, texts: Sequence[str], words: Sequence[str]) -> list[str]:
        return [word for word in words if any(word in text for text in texts)]

    def _first_matched_signal(self, texts: Sequence[str], words: Sequence[str]) -> str:
        matched = self._matched_signals(texts, words)
        return matched[0] if matched else ""

    def _infer_content_value_type(self, notes: Sequence) -> str:
        text = " ".join([note.title + " " + note.content for note in notes])
        if any(word in text for word in ["教程", "步骤", "方法", "攻略"]):
            return "实用干货型"
        if any(word in text for word in ["情绪", "焦虑", "治愈", "共鸣"]):
            return "情绪共鸣型"
        if any(word in text for word in ["娱乐", "搞笑", "快乐", "好玩"]):
            return "娱乐种草型"
        if any(word in text for word in ["测评", "对比", "避雷", "清单"]):
            return "决策参考型"
        return "经验分享型"

    def _infer_dominant_narrative(self, texts: Sequence[str]) -> str:
        joined = " ".join(texts)
        if any(word in joined for word in ["踩坑", "避雷", "后悔"]):
            return "踩坑复盘"
        if any(word in joined for word in ["亲测", "实测", "测评"]):
            return "亲测验证"
        if any(word in joined for word in ["清单", "合集", "推荐"]):
            return "清单推荐"
        return "经验分享"

    def _infer_emotional_arc(self, texts: Sequence[str]) -> str:
        matched = self._matched_signals(texts, self.emotion_words)
        if not matched:
            return "问题切入 -> 给出方法 -> 行动建议"
        return f"{matched[0]}切入 -> 经验验证 -> 降低焦虑"

    def _reusable_expressions(self, titles: Sequence[str]) -> list[str]:
        expressions: list[str] = []
        for title in titles:
            if re.search(r"\d", title):
                expressions.append("数字化标题")
            if any(word in title for word in ["必看", "建议收藏", "别再", "终于"]):
                expressions.append(title)
        return list(dict.fromkeys(expressions))[:8]

    def _best_post_features(self, note) -> list[str]:
        features: list[str] = []
        if re.search(r"\d", note.title):
            features.append("标题含数字")
        if any(word in note.title for word in self.recommendation_words):
            features.append("标题含高频钩子词")
        if note.tags:
            features.append("标签明确")
        if self.emoji_pattern.search(note.content):
            features.append("正文使用 emoji")
        if note.comments > 0:
            features.append("具备评论互动")
        return features

    def _title_formula(self, title_patterns: Sequence[str], keywords: Sequence[str]) -> str:
        hook = title_patterns[0] if title_patterns else "推荐"
        keyword = keywords[0] if keywords else "核心主题"
        return f"数字/场景 + {keyword} + {hook} + 明确收益"

    def _opening_strategy(self, core_user_pain: str, content_value_type: str) -> str:
        if core_user_pain:
            return f"开头先点出用户对「{core_user_pain}」的具体困惑，再给出结论。"
        return f"开头用一个真实场景引出内容价值，定位为{content_value_type}。"

    def _body_structure(
        self, content_value_type: str, credibility_signals: Sequence[str]
    ) -> list[str]:
        structure = ["先给结论", "拆成 3-5 个要点", "每个要点配具体理由或使用场景"]
        if credibility_signals:
            structure.append("补充亲测/对比/复盘证据")
        if content_value_type in {"实用干货型", "决策参考型"}:
            structure.append("结尾给出适用人群和避坑提醒")
        return structure

    def _credibility_tactics(self, credibility_signals: Sequence[str]) -> str:
        if credibility_signals:
            return f"优先使用{', '.join(credibility_signals[:3])}等表达增强可信度。"
        return "用真实场景、前后对比和具体细节增强可信度。"
