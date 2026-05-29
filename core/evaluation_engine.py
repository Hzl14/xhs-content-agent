import re

from core.text_postprocess import has_markdown_leak
from models.evaluation import PatternFeedback, ReviewCritique
from models.schemas import AnalysisResult, ContentItem


class HardGateChecker:
    banned_words = ["点击购买", "私信领取", "加微信", "扫码"]

    def check(self, content: ContentItem) -> tuple[bool, list[str]]:
        failures: list[str] = []
        title_len = len(content.title.strip())
        body_len = len(content.body.strip())
        hashtag_count = len([tag for tag in content.hashtags if tag.strip()])
        full_text = f"{content.title}\n{content.body}\n{content.cta}"

        if title_len < 8:
            failures.append("标题过短（<8字）")
        if title_len > 35:
            failures.append("标题过长（>35字）")
        if body_len < 150:
            failures.append("正文过短（<150字）")
        if body_len > 1500:
            failures.append("正文过长（>1500字）")
        if hashtag_count < 3:
            failures.append("标签不足（<3个）")
        if hashtag_count > 15:
            failures.append("标签过多（>15个）")

        for word in self.banned_words:
            if word in full_text:
                failures.append(f"含违禁词：{word}")

        return len(failures) == 0, failures


class EvaluationEngine:
    weights = {
        "hook_score": 0.25,
        "keyword_score": 0.15,
        "authenticity_score": 0.20,
        "format_score": 0.15,
        "cta_score": 0.10,
        "trend_alignment_score": 0.10,
        "audience_fit_score": 0.05,
    }
    synonym_map = {
        "防晒": ["防晒霜", "防晒乳", "防晒产品"],
        "护肤": ["皮肤护理", "保养", "护肤品"],
        "穿搭": ["搭配", "ootd", "通勤装"],
        "减脂": ["减肥", "控卡", "低脂"],
        "职场": ["上班", "工作", "通勤"],
    }
    emoji_pattern = re.compile(r"[\U0001F300-\U0001FAFF]")
    question_marks = ("?", "？")

    def __init__(self) -> None:
        self.hard_gate = HardGateChecker()

    def evaluate(self, content: ContentItem, analysis: AnalysisResult | None) -> ReviewCritique:
        gate_passed, gate_failures = self.hard_gate.check(content)
        if not gate_passed:
            critique = ReviewCritique(
                hard_gate_passed=False,
                gate_failures=gate_failures,
                total_score=0.0,
                weak_dimensions=["hard_gate"],
                issues=gate_failures,
                suggestions=["先修复基础格式、标签数量或违禁词问题，再进入评分和重写。"],
            )
            critique.pattern_feedback = PatternFeedback()
            return critique

        critique = ReviewCritique(hard_gate_passed=True)
        full_text = f"{content.title}\n{content.body}\n{content.cta}"

        critique.hook_score = self._score_hook(content=content, analysis=analysis)
        critique.keyword_score = self._score_keywords(full_text=full_text, analysis=analysis)
        critique.format_score = self._score_format(content=content)
        critique.cta_score = self._score_cta(content=content)
        critique.authenticity_score = self._score_authenticity(content=content, full_text=full_text, analysis=analysis)
        critique.trend_alignment_score = self._score_trend_alignment(
            content=content,
            full_text=full_text,
            analysis=analysis,
        )
        critique.audience_fit_score = self._score_audience_fit(full_text=full_text, analysis=analysis)
        critique.total_score = self._weighted_total(critique)

        self._append_feedback(critique=critique, analysis=analysis)
        return critique

    def _score_hook(self, content: ContentItem, analysis: AnalysisResult | None) -> float:
        title = content.title.strip()
        hook_words = analysis.structural_patterns.hook_words if analysis else []
        score = 0.0

        if re.search(r"\d+", title):
            score += 30.0

        matched_hooks = [word for word in hook_words if word and word in title]
        score += min(len(matched_hooks) * 10.0, 20.0)

        high_value_patterns = [
            r"(踩坑|避坑|后悔|亏了)",
            r"(没想到|原来|真相)",
            r"(必看|必备|必买|建议收藏)",
            r"(对比|测评|亲测|实测)",
        ]
        if any(re.search(pattern, title) for pattern in high_value_patterns):
            score += 30.0

        title_len = len(title)
        if 15 <= title_len <= 25:
            score += 20.0
        elif 10 <= title_len < 15 or 25 < title_len <= 30:
            score += 10.0

        return min(score, 100.0)

    def _score_keywords(self, full_text: str, analysis: AnalysisResult | None) -> float:
        keywords = analysis.top_keywords[:5] if analysis else []
        if not keywords:
            return 70.0

        matched = 0.0
        for keyword in keywords:
            if not keyword:
                continue
            if keyword in full_text:
                matched += 1.0
                continue
            synonyms = self.synonym_map.get(keyword, [])
            if any(synonym in full_text for synonym in synonyms):
                matched += 0.8

        coverage = matched / len(keywords)
        keyword_hits = sum(full_text.count(keyword) for keyword in keywords if keyword)
        keyword_density = keyword_hits / max(len(full_text), 1)
        if keyword_density > 0.05:
            coverage *= 0.7

        return round(min(coverage * 100.0, 100.0), 2)

    def _score_format(self, content: ContentItem) -> float:
        body = content.body.strip()
        full_text = f"{content.title}\n{content.body}\n{content.cta}"
        paragraphs = [p for p in re.split(r"\n+", body) if p.strip()]
        emoji_count = len(self.emoji_pattern.findall(body))
        body_len = max(len(body), 1)
        emoji_density = emoji_count / body_len * 100

        score = 0.0
        if 3 <= len(paragraphs) <= 8:
            score += 40.0
        elif len(paragraphs) >= 2:
            score += 25.0

        if 150 <= body_len <= 900:
            score += 35.0
        elif 900 < body_len <= 1500:
            score += 20.0

        if 0.2 <= emoji_density <= 2.0:
            score += 25.0
        elif emoji_density > 0:
            score += 12.0

        if has_markdown_leak(full_text):
            score -= 30.0

        return max(0.0, min(score, 100.0))

    def _score_cta(self, content: ContentItem) -> float:
        cta = content.cta.strip()
        if not cta:
            return 0.0
        score = 30.0
        if len(cta) >= 8:
            score += 20.0
        if cta.endswith(self.question_marks):
            score += 30.0
        if any(word in cta for word in ["评论", "告诉我", "收藏", "分享", "你", "一起"]):
            score += 20.0
        return min(score, 100.0)

    def _score_authenticity(
        self,
        content: ContentItem,
        full_text: str,
        analysis: AnalysisResult | None,
    ) -> float:
        if self._is_analysis_guide(content=content, full_text=full_text):
            return self._score_analysis_authenticity(full_text)

        signals = analysis.content_insights.credibility_signals if analysis else []
        baseline_words = ["亲测", "实测", "真实", "经验", "对比", "复盘", "踩坑", "总结"]
        detail_words = ["时间", "价格", "预算", "前后", "用下来", "实际", "适合", "不适合"]
        matched_signals = sum(1 for word in set([*signals, *baseline_words]) if word and word in full_text)
        matched_details = sum(1 for word in detail_words if word in full_text)

        score = min(matched_signals, 3) * 20.0 + min(matched_details, 3) * 10.0
        if any(word in full_text for word in ["我", "自己"]):
            score += 10.0
        return min(score, 100.0)

    @staticmethod
    def _is_analysis_guide(content: ContentItem, full_text: str) -> bool:
        text = f"{content.content_type} {full_text}"
        markers = ["分析", "误区", "观点", "思考", "现象", "本质", "为什么", "怎么看", "恋爱观"]
        return any(marker in text for marker in markers)

    @staticmethod
    def _score_analysis_authenticity(full_text: str) -> float:
        argument_words = [
            "误区",
            "本质",
            "原因",
            "边界",
            "尊重",
            "沟通",
            "独立",
            "情绪价值",
            "亲密关系",
            "不是",
            "而是",
            "真正",
        ]
        structure_words = ["第一", "第二", "第三", "1", "2", "3", "首先", "其次", "最后"]
        action_words = ["可以", "建议", "判断", "避免", "学会", "保持", "建立"]
        fabricated_personal_patterns = [
            r"我去年",
            r"我大三",
            r"我分手",
            r"我前任",
            r"我男友",
            r"我女友",
            r"我花了\d+天",
            r"\d+天自救",
            r"拿到\d+个offer",
        ]

        score = 45.0
        score += min(sum(1 for word in argument_words if word in full_text), 6) * 6.0
        score += min(sum(1 for word in structure_words if word in full_text), 3) * 4.0
        score += min(sum(1 for word in action_words if word in full_text), 3) * 3.0
        if any(re.search(pattern, full_text) for pattern in fabricated_personal_patterns):
            score -= 35.0
        return max(0.0, min(score, 100.0))

    def _score_trend_alignment(
        self,
        content: ContentItem,
        full_text: str,
        analysis: AnalysisResult | None,
    ) -> float:
        if analysis is None:
            return 70.0

        trend_terms = [
            *analysis.structural_patterns.top_keywords[:5],
            *analysis.structural_patterns.top_tags[:5],
            *analysis.structural_patterns.hook_words[:5],
            analysis.engagement_signals.content_value_type,
            analysis.content_insights.dominant_narrative,
        ]
        matched = sum(1 for term in trend_terms if term and term in full_text)
        score = min(100.0, matched * 12.0)

        content_type = analysis.engagement_signals.content_value_type
        if content_type and content.content_type and content_type[:2] in content.content_type:
            score += 10.0
        return min(score, 100.0)

    def _score_audience_fit(self, full_text: str, analysis: AnalysisResult | None) -> float:
        must_include = analysis.writing_strategy.must_include_elements if analysis else []
        avoid_patterns = analysis.writing_strategy.avoid_patterns if analysis else []
        matched = sum(1 for item in must_include[:5] if item and item in full_text)
        violations = sum(1 for item in avoid_patterns if item and item in full_text)

        score = min(matched, 3) * 20.0 - violations * 20.0
        if any(word in full_text for word in ["适合", "不适合", "新手", "学生党", "上班族", "预算"]):
            score += 40.0
        return max(0.0, min(score, 100.0))

    def _weighted_total(self, critique: ReviewCritique) -> float:
        return round(
            critique.hook_score * self.weights["hook_score"]
            + critique.keyword_score * self.weights["keyword_score"]
            + critique.authenticity_score * self.weights["authenticity_score"]
            + critique.format_score * self.weights["format_score"]
            + critique.cta_score * self.weights["cta_score"]
            + critique.trend_alignment_score * self.weights["trend_alignment_score"]
            + critique.audience_fit_score * self.weights["audience_fit_score"],
            2,
        )

    def _append_feedback(self, critique: ReviewCritique, analysis: AnalysisResult | None) -> None:
        if critique.hook_score < 75:
            critique.weak_dimensions.append("hook_score")
            critique.issues.append("标题吸引力不足")
            critique.suggestions.append("标题中加入数字、热帖钩子词或明确收益点")
        if critique.keyword_score < 75:
            critique.weak_dimensions.append("keyword_score")
            critique.issues.append("关键词/SEO 覆盖不足")
            critique.suggestions.append("自然加入分析得到的高频关键词，避免机械堆词")
        if critique.format_score < 75:
            critique.weak_dimensions.append("format_score")
            critique.issues.append("排版可读性偏弱")
            critique.suggestions.append("拆成更清晰的段落，并加入少量相关 emoji")
        if critique.cta_score < 75:
            critique.weak_dimensions.append("cta_score")
            critique.issues.append("互动引导不足")
            critique.suggestions.append("结尾加入面向读者的问题或收藏/评论引导")
        if critique.authenticity_score < 75:
            critique.weak_dimensions.append("authenticity_score")
            critique.issues.append("真实感不足")
            critique.suggestions.append("补充亲测、对比、踩坑、时间、预算或适用场景等细节")
        if critique.trend_alignment_score < 75:
            critique.weak_dimensions.append("trend_alignment_score")
            critique.issues.append("与热帖趋势贴合度不足")
            critique.suggestions.append("贴合分析中的内容价值类型、标题模式和高频标签")
        if critique.audience_fit_score < 75:
            critique.weak_dimensions.append("audience_fit_score")
            critique.issues.append("目标受众适配度不足")
            if analysis and analysis.writing_strategy.must_include_elements:
                critique.suggestions.append(
                    "补充这些受众关心的元素："
                    + "、".join(analysis.writing_strategy.must_include_elements[:3])
                )
            else:
                critique.suggestions.append("明确适用人群、使用场景和不适合的人群")

        critique.pattern_feedback = self._build_pattern_feedback(critique)

    def _build_pattern_feedback(self, critique: ReviewCritique) -> PatternFeedback:
        dimension_rules = {
            "hook_score": "标题必须在前半句出现数字、场景、痛点或明确收益，禁止空泛标题。",
            "keyword_score": "正文必须自然覆盖核心关键词，禁止只在标签里出现关键词或机械堆词。",
            "format_score": "正文必须分段清晰，每段只表达一个重点，并使用少量相关 emoji 增强可读性。",
            "cta_score": "结尾必须有面向读者的问题或收藏/评论引导，禁止无互动直接结束。",
            "authenticity_score": "观点必须有真实细节支撑，例如亲测、对比、踩坑、时间、预算或适用场景。",
            "trend_alignment_score": "内容必须贴合热帖分析中的内容价值类型、标题模式、高频标签和用户痛点。",
            "audience_fit_score": "正文必须明确适用人群、使用场景或不适合的人群，避免泛泛而谈。",
        }
        failed_patterns = {
            "hook_score": "标题缺少数字、痛点、场景或明确收益。",
            "keyword_score": "正文没有自然覆盖核心关键词，或存在关键词堆砌风险。",
            "format_score": "正文排版不够清晰，阅读负担偏高。",
            "cta_score": "结尾缺少评论、收藏或提问式互动引导。",
            "authenticity_score": "表达缺少真实使用细节或判断依据。",
            "trend_alignment_score": "内容没有明显贴合当前热帖趋势。",
            "audience_fit_score": "内容没有清楚说明适合谁、什么场景适用。",
        }
        successful_patterns = {
            "hook_score": "标题具备数字、痛点、场景或明确收益。",
            "keyword_score": "正文自然覆盖了核心关键词且没有明显堆砌。",
            "format_score": "正文段落结构清晰，适合移动端阅读。",
            "cta_score": "结尾有明确互动引导。",
            "authenticity_score": "内容包含真实细节或判断依据。",
            "trend_alignment_score": "内容贴合热帖趋势和分析策略。",
            "audience_fit_score": "内容说明了适用人群或使用场景。",
        }

        weak = set(critique.weak_dimensions)
        strong = [
            name
            for name in dimension_rules
            if name not in weak and getattr(critique, name, 0.0) >= 85
        ]
        rules = {name: dimension_rules[name] for name in critique.weak_dimensions}
        priority = critique.weak_dimensions[0] if critique.weak_dimensions else ""
        return PatternFeedback(
            failed_patterns=[failed_patterns[name] for name in critique.weak_dimensions],
            successful_patterns=[successful_patterns[name] for name in strong[:3]],
            dimension_rules=rules,
            top_priority=dimension_rules.get(priority, ""),
        )
