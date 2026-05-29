import re

from core.agent_base import BaseAgent
from core.text_postprocess import clean_content_item
from models.prompts import CONTENT_PROMPT, SHARED_CONTEXT_HEADER
from models.schemas import (
    AnalysisResult,
    ContentItem,
    GeneratedTopicWithContents,
    NodeTrace,
    PipelineState,
)
from services.llm_service import LLMService


class ContentAgent(BaseAgent):
    name = "content_generator"

    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        if not state.topics:
            raise ValueError("Topics are required before content generation.")

        generated: list[GeneratedTopicWithContents] = []
        token_in = 0
        token_out = 0

        for topic in state.topics:
            contents = await self._generate_for_topic(state, topic.title, topic.reason)
            generated.append(GeneratedTopicWithContents(topic=topic, contents=contents))
            token_in += state.metadata.get("last_input_tokens", 0)
            token_out += state.metadata.get("last_output_tokens", 0)

        trace.input_tokens = token_in
        trace.output_tokens = token_out
        state.results = generated
        trace.status = "success"
        return state

    async def _generate_for_topic(self, state: PipelineState, topic: str, reason: str) -> list[ContentItem]:
        if not self.llm_service.enabled:
            return self._fallback_contents(state, topic)

        shared_context = (
            f"{SHARED_CONTEXT_HEADER}\n{state.llm_context}" if state.llm_context else SHARED_CONTEXT_HEADER
        )
        system = CONTENT_PROMPT.system
        content_mode = self._infer_content_mode(state, topic, reason)
        state.metadata["content_mode"] = content_mode
        state.metadata["has_user_material"] = self._has_user_material(state.user_message)

        user = CONTENT_PROMPT.render_user(
            shared_context=shared_context,
            analysis_brief=self._build_analysis_brief(state.analysis),
            topic=topic,
            reason=reason,
            audience=state.audience,
            tone=state.tone,
            content_count=state.content_count_per_topic,
        )
        user = f"{user}\n\n{self._build_generation_guardrails(state, topic, content_mode)}"
        try:
            result = await self.llm_service.chat_json(system=system, user=user)
        except Exception as exc:  # noqa: BLE001
            state.metadata["content_llm_fallback_reason"] = str(exc)
            return self._fallback_contents(state, topic)
        state.metadata["last_input_tokens"] = result.input_tokens
        state.metadata["last_output_tokens"] = result.output_tokens
        parsed = self.llm_service.extract_json(result.content) or {}

        contents: list[ContentItem] = []
        for item in parsed.get("contents", [])[: state.content_count_per_topic]:
            try:
                content = clean_content_item(ContentItem(**item))
                if (
                    not state.metadata.get("has_user_material")
                    and self._contains_unverified_personal_story(content)
                ):
                    state.metadata["rejected_unverified_personal_story"] = True
                    continue
                contents.append(content)
            except Exception:  # noqa: BLE001
                continue

        if not contents:
            return self._safe_no_material_contents(state, topic, content_mode)
        return contents

    @classmethod
    def _infer_content_mode(cls, state: PipelineState, topic: str, reason: str) -> str:
        text = f"{state.user_message} {state.search_query} {topic} {reason}"
        personal_markers = [
            "我的经历",
            "亲身经历",
            "亲测",
            "我试过",
            "我用过",
            "经历分享",
            "我的复盘",
            "我的踩坑",
            "以我的",
            "按我的",
            "根据我的",
            "减肥经历",
            "转行故事",
        ]
        analysis_markers = [
            "误区",
            "分析",
            "思考",
            "怎么看",
            "观点",
            "现象",
            "为什么",
            "在哪里",
            "正能量",
            "深度",
            "恋爱观",
        ]
        review_markers = ["测评", "推荐", "种草", "打卡", "好物", "探店", "使用体验"]
        if any(marker in text for marker in personal_markers):
            return "personal_experience"
        if any(marker in text for marker in analysis_markers):
            return "analysis_guide"
        if any(marker in text for marker in review_markers):
            return "review_recommendation"
        return "general_guide"

    @staticmethod
    def _has_user_material(user_message: str) -> bool:
        text = user_message or ""
        if any(marker in text for marker in ["没有素材", "没素材", "没有经历", "没经历", "跳过"]):
            return False
        normal_patterns = [
            r"(我的|亲身).*(经历|故事|案例|素材)",
            r"我(去年|今年|当时|曾经|之前|最近|已经|刚刚)",
            r"我.*(投了|拿到|分手|复合|瘦了|胖了|买了|用了|用过|去了|试过|做过|遇到)",
            r"我.*\d+\s*(天|个月|年|次|份|斤|offer|块|元)",
        ]
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in normal_patterns):
            return True
        material_patterns = [
            r"我(去年|今年|当时|曾经|之前|最近|已经|刚刚)",
            r"我.*(投了|拿到|分手|复合|瘦了|胖了|买了|用了|去了|试过|做过|遇到)",
            r"(我的|亲身).*(经历|故事|案例|素材)",
            r"\d+\s*(天|个月|年|次|份|斤|offer|块|元)",
        ]
        has_pattern = any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in material_patterns)
        if not has_pattern:
            return False
        real_markers = [
            "我的经历",
            "亲身经历",
            "我去年",
            "我今年",
            "我当时",
            "我曾经",
            "我之前",
            "我最近",
            "我投了",
            "我拿到",
            "我分手",
            "我用过",
            "我试过",
            "我去了",
            "我买了",
        ]
        return any(marker in text for marker in real_markers)

    @staticmethod
    def _build_generation_guardrails(state: PipelineState, topic: str, content_mode: str) -> str:
        has_material = bool(state.metadata.get("has_user_material"))
        no_material_rules = ""
        if not has_material:
            no_material_rules = """
用户没有提供真实亲身素材：
- 严禁编造第一人称亲历、具体年龄、天数、恋爱/求职/减肥等个人结果。
- 不要写“我去年/我分手后/我拿到offer/我亲测有效”这类未经用户提供的事实。
- 可以写成观察型、分析型、方法型内容，用“很多人/常见情况/可以这样判断”表达。
"""
        analysis_rules = ""
        if content_mode == "analysis_guide":
            analysis_rules = f"""
本次是观点分析/误区拆解型内容，核心主题是：{topic}
- 必须围绕用户核心诉求展开，不要把主题替换成个人故事。
- 正文要回答“误区是什么、为什么会这样、怎样更健康地看待/处理”。
- 可以引用热帖里常见表达和经验，但只能作为观点依据，不能包装成作者亲身经历。
"""
        return f"""
【生成硬约束】
内容模式：{content_mode}
- 禁止使用任何 Markdown 语法，包括 **、##、*、[]()、```；小红书会直接显示这些符号。
- 标题、正文、CTA 都输出纯文本和少量自然 emoji。
- 两篇候选必须换角度：不要重复同一年龄、天数、故事线、标题公式。
{no_material_rules}
{analysis_rules}
""".strip()

    @staticmethod
    def _contains_unverified_personal_story(content: ContentItem) -> bool:
        full_text = f"{content.title}\n{content.body}\n{content.cta}"
        patterns = [
            r"我(去年|今年|大一|大二|大三|大四|上周|昨天|前任|男友|女友|分手|复合)",
            r"我.*(花了|用了|坚持了)\s*\d+\s*(天|个月|年)",
            r"\d+\s*天(自救|逆袭|复盘)",
            r"拿到\s*\d+\s*个\s*offer",
            r"瘦了\s*\d+\s*斤",
            r"投了\s*\d+\s*份",
        ]
        return any(re.search(pattern, full_text, flags=re.IGNORECASE) for pattern in patterns)

    @staticmethod
    def _safe_no_material_contents(
        state: PipelineState,
        topic: str,
        content_mode: str,
    ) -> list[ContentItem]:
        tags = []
        if state.analysis:
            tags = [*state.analysis.top_tags[:3], *state.analysis.top_keywords[:3]]
        hashtags = [topic.replace(" ", ""), *tags]
        seen: list[str] = []
        for tag in hashtags:
            tag = tag.strip().lstrip("#")
            if tag and tag not in seen:
                seen.append(tag)
        if content_mode == "analysis_guide":
            title = f"{topic}：别再把这3件事当成成熟"
            body = (
                f"聊 {topic}，最重要的不是写一个戏剧化故事，而是把常见误区讲清楚。\n\n"
                "1. 把控制当安全感\n"
                "真正稳定的关系，不靠随时报备和秒回证明爱，而靠边界、信任和稳定沟通。\n\n"
                "2. 把消耗当深情\n"
                "如果一段关系让人长期焦虑、失眠、怀疑自己，它就不是爱的浓度高，而是相处方式需要调整。\n\n"
                "3. 把完美匹配当标准答案\n"
                "健康关系不是两个人完全一样，而是能表达不同、尊重差异，也愿意一起解决问题。\n\n"
                "正能量的恋爱观不是劝人忍，也不是劝人冷漠，而是先成为稳定的自己，再进入互相滋养的关系。"
            )
            cta = "你觉得年轻人恋爱里最容易踩的误区是什么？评论区聊聊"
            content_type = "分析指南"
        else:
            title = f"{topic}别硬写亲身经历，先讲清这3点"
            body = (
                f"没有真实素材时，{topic} 更适合写成方法型内容，不要硬编“我亲测”。\n\n"
                "1. 先讲适用人群\n"
                "告诉读者这篇内容适合谁、不适合谁，比空泛讲大道理更有用。\n\n"
                "2. 再讲判断标准\n"
                "把选择、避坑、执行步骤拆开，让读者能直接对照自己的情况。\n\n"
                "3. 最后讲风险提醒\n"
                "不要只写好处，也要写限制条件和容易误解的地方，这样内容会更可信。"
            )
            cta = "你想看更偏方法清单，还是更偏案例拆解？评论区告诉我"
            content_type = "方法指南"
        return [
            clean_content_item(
                ContentItem(
                    title=title,
                    body=body,
                    hashtags=seen[:6] or [topic.replace(" ", ""), "方法论", "避坑"],
                    cta=cta,
                    image_suggestion=f"{topic} 文字封面，关键词突出",
                    content_type=content_type,
                )
            )
        ]

    @staticmethod
    def _build_analysis_brief(analysis: AnalysisResult | None) -> str:
        if analysis is None:
            return "暂无热帖分析结果，请按当前选题和用户约束生成。"

        structural = analysis.structural_patterns
        insights = analysis.content_insights
        engagement = analysis.engagement_signals
        strategy = analysis.writing_strategy

        return "\n".join(
            [
                f"分析摘要：{analysis.summary}",
                f"样本量：{analysis.sample_size}",
                f"高频关键词：{', '.join(structural.top_keywords[:8]) or '暂无'}",
                f"高频标签：{', '.join(structural.top_tags[:8]) or '暂无'}",
                f"标题模式/钩子词：{', '.join(structural.hook_words[:6]) or '暂无'}",
                f"平均标题长度：{structural.avg_title_length}",
                f"平均段落数：{structural.avg_paragraph_count}",
                f"是否常用数字标题：{structural.uses_numbering}",
                f"是否常用 emoji：{structural.uses_emoji}",
                f"是否常用提问结尾：{structural.ends_with_question}",
                f"主叙事：{insights.dominant_narrative or '暂无'}",
                f"核心痛点：{insights.core_user_pain or '暂无'}",
                f"可信度信号：{', '.join(insights.credibility_signals[:6]) or '暂无'}",
                f"情绪路径：{insights.emotional_arc or '暂无'}",
                f"可复用表达：{', '.join(insights.reusable_expressions[:6]) or '暂无'}",
                f"内容价值类型：{engagement.content_value_type or '暂无'}",
                f"最佳样本标题：{engagement.best_post_title or '暂无'}",
                f"最佳样本特征：{', '.join(engagement.best_post_key_features[:6]) or '暂无'}",
                f"推荐标题公式：{strategy.recommended_title_formula or '暂无'}",
                f"开头策略：{strategy.opening_strategy or '暂无'}",
                f"正文结构：{'; '.join(strategy.body_structure) or '暂无'}",
                f"可信度打法：{strategy.credibility_tactics or '暂无'}",
                f"结尾 CTA：{strategy.closing_cta or '暂无'}",
                f"必含元素：{', '.join(strategy.must_include_elements[:8]) or '暂无'}",
                f"避免模式：{', '.join(strategy.avoid_patterns[:8]) or '暂无'}",
            ]
        )

    @staticmethod
    def _fallback_contents(state: PipelineState, topic: str) -> list[ContentItem]:
        analysis = state.analysis
        keywords = analysis.top_keywords[:3] if analysis else []
        tags = analysis.top_tags[:3] if analysis else []
        strategy = analysis.writing_strategy if analysis else None
        title_formula = strategy.recommended_title_formula if strategy else ""
        body_structure = strategy.body_structure if strategy else []
        opening = strategy.opening_strategy if strategy else ""
        cta = (
            strategy.closing_cta
            if strategy and strategy.closing_cta
            else "你更想看学生党版还是进阶版？评论区告诉我。"
        )
        content_type = (
            analysis.engagement_signals.content_value_type
            if analysis and analysis.engagement_signals.content_value_type
            else "分享"
        )

        contents: list[ContentItem] = []
        for i in range(state.content_count_per_topic):
            keyword_text = "、".join(keywords) or topic
            title_prefix = f"{i + 3}个重点" if analysis and analysis.structural_patterns.uses_numbering else "实用清单"
            body_lines = [
                opening or f"今天做一版 {topic} 的真实分享，先说结论，再说步骤。",
                f"这篇会围绕 {keyword_text} 展开，尽量按热帖里高频出现的问题来写。",
            ]
            if body_structure:
                body_lines.append("正文结构：" + "；".join(body_structure[:4]) + "。")
            else:
                body_lines.append("先看场景，再看匹配度，最后看避坑点。")
            if strategy and strategy.credibility_tactics:
                body_lines.append(strategy.credibility_tactics)
            body_lines.append("我会把适用人群和踩坑点写清楚，方便直接对照。✨")

            contents.append(
                clean_content_item(ContentItem(
                    title=f"{title_prefix}｜{topic}：{title_formula or '按热帖规则写'}",
                    body="\n\n".join(body_lines),
                    hashtags=[topic.replace(" ", ""), *tags, *keywords[:2]][:6],
                    cta=cta,
                    image_suggestion=f"{topic}场景化生活方式图片",
                    content_type=content_type,
                ))
            )
        return contents
