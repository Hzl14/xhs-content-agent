from __future__ import annotations

import re

from pydantic import ValidationError

from core.agent_base import BaseAgent
from models.prompts import PLANNER_PROMPT, SHARED_CONTEXT_HEADER
from models.schemas import (
    NodeTrace,
    PipelinePlan,
    PipelineState,
    PlannedStageItem,
    TopicItem,
)
from models.states import PipelineStage, PlannedStageStatus
from services.llm_service import LLMService


INTENT_CRAWL_ONLY = "crawl_only"
INTENT_TOPIC_ONLY = "topic_only"
INTENT_COPYWRITING_ONLY = "copywriting_only"
INTENT_FULL_POST = "full_post"
INTENT_PUBLISH_POST = "publish_post"
INTENT_CLARIFY = "clarify_request"

SUPPORTED_INTENTS = {
    INTENT_CRAWL_ONLY,
    INTENT_TOPIC_ONLY,
    INTENT_COPYWRITING_ONLY,
    INTENT_FULL_POST,
    INTENT_PUBLISH_POST,
    INTENT_CLARIFY,
}

INTENT_REQUIRED_FIELDS = {
    INTENT_CRAWL_ONLY: ["topic"],
    INTENT_TOPIC_ONLY: ["topic"],
    INTENT_COPYWRITING_ONLY: ["topic"],
    INTENT_FULL_POST: ["topic"],
    INTENT_PUBLISH_POST: ["topic"],
}

TOPIC_HINTS = [
    "护肤", "保湿", "秋冬", "穿搭", "减肥", "减脂", "减脂餐", "健身", "考研", "留学", "求职", "找工作", "工作渠道", "求职渠道", "招聘", "面试",
    "探店", "旅游", "美食", "副业", "读书", "学习", "装修", "租房", "防晒",
    "化妆", "香水", "洗面奶", "爽肤水", "面霜", "精华", "相机", "产品", "课程",
    "宠物", "男生", "女生", "护发", "口红", "隔离", "粉底",
    "护肤", "穿搭", "减肥", "健身", "考研", "留学", "求职", "简历", "面试",
    "探店", "旅游", "美食", "副业", "读书", "学习", "装修", "租房", "防晒",
    "化妆", "香水", "洗面奶", "爽肤水", "面霜", "精华", "相机", "产品", "课程",
    "洗发水", "宠物", "男生", "女生", "护发", "身体乳", "口红", "隔离", "粉底",
]

GOAL_HINTS = [
    "做一期", "内容", "写", "生成", "帮我", "文案", "帖子", "笔记", "选题", "话题",
    "发布", "种草", "推荐", "测评", "攻略", "教程", "避坑", "分享", "完整",
    "查", "查一下", "经验贴",
    "搜", "爬", "找", "检索", "推荐", "测评", "攻略", "教程", "避坑", "分享",
    "文案", "帖子", "笔记", "选题", "话题", "发布", "种草", "清单",
]

AUDIENCE_HINTS = [
    "大学生", "学生党", "职场新人", "宝妈", "男生", "女生", "新手", "小白", "上班族",
]

TONE_HINTS = [
    "真实分享", "避坑", "测评", "攻略", "教程", "种草", "推荐", "对比",
    "真实分享", "避坑", "测评", "攻略", "教程", "种草", "推荐", "对比",
]


class PlannerAgent(BaseAgent):
    name = "planner"
    max_plan_attempts = 3

    def __init__(self, llm_service: LLMService) -> None:
        self.llm_service = llm_service

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        if not self.llm_service.enabled:
            plan = self._fallback_plan(state)
            plan = self._finalize_plan(state, plan)
            self._apply_plan(state, plan)
            trace.status = "success"
            return state

        shared_context = (
            f"{SHARED_CONTEXT_HEADER}\n{state.llm_context}" if state.llm_context else SHARED_CONTEXT_HEADER
        )
        shared_context = f"workflow_mode: {state.mode}\n{shared_context}"
        user = PLANNER_PROMPT.render_user(
            shared_context=shared_context,
            audience=state.audience,
            tone=state.tone,
            topic_count=state.topic_count,
            content_count_per_topic=state.content_count_per_topic,
            raw_crawl_limit=state.raw_crawl_limit,
            final_note_limit=state.final_note_limit,
            mode=state.mode,
        )
        try:
            plan, input_tokens, output_tokens, errors = await self._generate_plan(state, user)
        except Exception as exc:
            plan = None
            input_tokens = 0
            output_tokens = 0
            errors = [f"llm_error: {exc}"]
        trace.input_tokens = input_tokens
        trace.output_tokens = output_tokens
        trace.retry_count = max(0, len(errors) - 1)
        if errors:
            state.metadata["planner_attempt_errors"] = errors

        if plan is None:
            plan = self._fallback_plan(state)
            state.metadata["planner_fallback_reason"] = errors[-1] if errors else "empty_plan"

        plan = self._finalize_plan(state, plan)
        self._apply_plan(state, plan)
        trace.status = "success"
        return state

    async def _generate_plan(
        self,
        state: PipelineState,
        user: str,
    ) -> tuple[PipelinePlan | None, int, int, list[str]]:
        input_tokens = 0
        output_tokens = 0
        errors: list[str] = []
        prompt = user

        for attempt in range(1, self.max_plan_attempts + 1):
            result = await self.llm_service.chat(system=PLANNER_PROMPT.system, user=prompt)
            input_tokens += result.input_tokens
            output_tokens += result.output_tokens

            parsed = self.llm_service.extract_json(result.content)
            if parsed is None:
                errors.append(f"attempt_{attempt}: invalid_json")
                prompt = self._build_retry_prompt(user, errors[-1])
                continue

            try:
                plan = PipelinePlan(**parsed)
            except ValidationError as exc:
                errors.append(f"attempt_{attempt}: validation_error: {exc.errors()}")
                prompt = self._build_retry_prompt(user, errors[-1])
                continue

            quality_error = self._validate_plan_quality(state, plan)
            if quality_error:
                errors.append(f"attempt_{attempt}: {quality_error}")
                prompt = self._build_retry_prompt(user, errors[-1])
                continue

            return plan, input_tokens, output_tokens, errors

        return None, input_tokens, output_tokens, errors

    @staticmethod
    def _build_retry_prompt(original_user: str, error: str) -> str:
        return (
            f"{original_user}\n\n"
            "[上一次计划输出不可执行]\n"
            f"错误：{error}\n"
            "请重新输出一个完整 JSON 对象。必须包含 "
            "intent、needs_clarification、clarification_question、clarification_fields、"
            "clarification_tips、topic_seed、planned_stages、needs_crawl、needs_analysis、"
            "needs_topic_generation、needs_content_generation、needs_review、needs_publish、"
            "search_query、search_keywords、audience、tone、topic_count、content_count_per_topic。"
            "不要输出 Markdown，不要解释。"
        )

    @staticmethod
    def _validate_plan_quality(state: PipelineState, plan: PipelinePlan) -> str | None:
        intent = PlannerAgent._normalize_intent(plan.intent, state.user_message)
        if intent == INTENT_CLARIFY and not plan.clarification_question.strip():
            return "missing_clarification_question"
        if intent != INTENT_CLARIFY and plan.needs_crawl and not PlannerAgent._normalize_keywords(plan.search_keywords):
            return "missing_search_keywords_for_crawl"
        if plan.needs_content_generation and not plan.needs_topic_generation and intent in {
            INTENT_FULL_POST,
            INTENT_PUBLISH_POST,
        }:
            return "full_post_requires_topic_generation"
        return None

    @staticmethod
    def _finalize_plan(state: PipelineState, plan: PipelinePlan) -> PipelinePlan:
        plan.intent = PlannerAgent._normalize_intent(plan.intent, state.user_message)
        has_explicit_user_message = bool(state.metadata.get("has_explicit_user_message"))
        if (
            plan.intent == INTENT_CLARIFY
            and PlannerAgent._has_goal_hint(state.user_message)
            and PlannerAgent._has_topic_hint(state.user_message, plan)
        ):
            plan.intent = PlannerAgent._normalize_intent("", state.user_message)
            if plan.intent == INTENT_CLARIFY:
                plan.intent = INTENT_FULL_POST
        if (
            state.candidate_notes
            and not has_explicit_user_message
            and plan.intent in {INTENT_CLARIFY, INTENT_TOPIC_ONLY, INTENT_CRAWL_ONLY, INTENT_COPYWRITING_ONLY}
        ):
            plan.intent = INTENT_FULL_POST
        if PlannerAgent._looks_like_research_or_collection_request(state.user_message):
            plan.intent = INTENT_CRAWL_ONLY
        plan.search_query = (plan.search_query or state.search_query or state.user_message).strip()
        plan.search_keywords = PlannerAgent._normalize_keywords(plan.search_keywords)
        if "考研" in state.user_message and ("中科大" in state.user_message or "中国科学技术大学" in state.user_message):
            year_prefix = "26" if "26" in state.user_message or "2026" in state.user_message else ""
            plan.search_query = f"{year_prefix}考研中科大经验贴".strip()
            plan.search_keywords = PlannerAgent._normalize_keywords(
                [f"{year_prefix}考研上岸中科大", "中科大考研经验贴"]
            )
        plan.clarification_fields = PlannerAgent._normalize_keywords(plan.clarification_fields, max_keywords=5)
        plan.topic_seed = (plan.topic_seed or PlannerAgent._infer_topic_seed(state.user_message, plan.search_query)).strip()
        if plan.intent == INTENT_CLARIFY and PlannerAgent._is_actionable_generation_request(state.user_message, plan):
            plan.intent = PlannerAgent._normalize_intent("", state.user_message)
            if plan.intent == INTENT_CLARIFY:
                plan.intent = INTENT_FULL_POST
        if state.candidate_notes:
            plan.search_query = plan.search_query or "provided notes"
            plan.topic_seed = plan.topic_seed or plan.search_query

        if not plan.search_keywords and plan.intent != INTENT_CLARIFY:
            plan.search_keywords = PlannerAgent._extract_keywords_rule_based(plan.search_query)
        if not plan.audience:
            plan.audience = PlannerAgent._infer_audience(state.user_message) or state.audience
        if not plan.tone:
            plan.tone = PlannerAgent._infer_tone(state.user_message) or state.tone

        plan.topic_count = max(1, min(int(plan.topic_count or state.topic_count), 10))
        plan.content_count_per_topic = max(1, min(int(plan.content_count_per_topic or state.content_count_per_topic), 5))

        if state.candidate_notes or plan.intent == INTENT_CRAWL_ONLY:
            missing_fields = []
        else:
            missing_fields = PlannerAgent._missing_required_fields(state.user_message, plan)
        if missing_fields:
            plan.intent = INTENT_CLARIFY
            plan.needs_clarification = True
            plan.clarification_fields = missing_fields
            plan.clarification_question = PlannerAgent._build_clarification_question(missing_fields)
            plan.clarification_tips = "信息越完整，生成内容质量越高。"
            plan.needs_crawl = False
            plan.needs_analysis = False
            plan.needs_topic_generation = False
            plan.needs_content_generation = False
            plan.needs_review = False
            plan.needs_publish = False
        else:
            plan.needs_clarification = False
            plan.clarification_question = ""
            plan.clarification_fields = []
            plan.clarification_tips = ""

        PlannerAgent._align_plan_with_intent(plan)
        plan.planned_stages = PlannerAgent._build_stage_items(plan)
        return plan

    @staticmethod
    def _apply_plan(state: PipelineState, plan: PipelinePlan) -> None:
        state.plan = plan
        state.search_query = plan.search_query
        state.search_keywords = plan.search_keywords
        state.audience = plan.audience
        state.tone = plan.tone
        state.topic_count = plan.topic_count
        state.content_count_per_topic = plan.content_count_per_topic
        if plan.intent == INTENT_COPYWRITING_ONLY and plan.topic_seed and not state.topics:
            state.topics = [TopicItem(title=plan.topic_seed, reason="用户直接指定的写作主题")]
        dump = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        state.metadata["pipeline_plan"] = dump

    @staticmethod
    def _fallback_plan(state: PipelineState) -> PipelinePlan:
        text = state.user_message.strip()
        intent = PlannerAgent._normalize_intent("", text)
        search_query = state.search_query or text
        return PipelinePlan(
            intent=intent,
            search_query=search_query,
            search_keywords=state.search_keywords or PlannerAgent._extract_keywords_rule_based(search_query),
            audience=PlannerAgent._infer_audience(text) or state.audience,
            tone=PlannerAgent._infer_tone(text) or state.tone,
            topic_seed=PlannerAgent._infer_topic_seed(text, search_query),
            topic_count=state.topic_count,
            content_count_per_topic=state.content_count_per_topic,
        )

    @staticmethod
    def _align_plan_with_intent(plan: PipelinePlan) -> None:
        intent = plan.intent
        if intent == INTENT_CLARIFY:
            return
        if intent == INTENT_CRAWL_ONLY:
            plan.needs_crawl = True
            plan.needs_analysis = True
            plan.needs_topic_generation = False
            plan.needs_content_generation = False
            plan.needs_review = False
            plan.needs_publish = False
            return
        if intent == INTENT_TOPIC_ONLY:
            plan.needs_crawl = True
            plan.needs_analysis = True
            plan.needs_topic_generation = True
            plan.needs_content_generation = False
            plan.needs_review = False
            plan.needs_publish = False
            return
        if intent == INTENT_COPYWRITING_ONLY:
            plan.needs_crawl = False
            plan.needs_analysis = False
            plan.needs_topic_generation = False
            plan.needs_content_generation = True
            plan.needs_review = False
            plan.needs_publish = False
            return
        if intent == INTENT_FULL_POST:
            plan.needs_crawl = True
            plan.needs_analysis = True
            plan.needs_topic_generation = True
            plan.needs_content_generation = True
            plan.needs_review = True
            plan.needs_publish = False
            return
        if intent == INTENT_PUBLISH_POST:
            plan.needs_crawl = True
            plan.needs_analysis = True
            plan.needs_topic_generation = True
            plan.needs_content_generation = True
            plan.needs_review = True
            plan.needs_publish = True

    @staticmethod
    def _build_stage_items(plan: PipelinePlan) -> list[PlannedStageItem]:
        ordered = [
            (PipelineStage.CRAWLING, plan.needs_crawl),
            (PipelineStage.ANALYZING, plan.needs_analysis),
            (PipelineStage.TOPIC_GENERATING, plan.needs_topic_generation),
            (PipelineStage.CONTENT_GENERATING, plan.needs_content_generation),
            (PipelineStage.REVIEWING, plan.needs_review),
            (PipelineStage.PUBLISHING, plan.needs_publish),
        ]
        items: list[PlannedStageItem] = []
        ready_assigned = False
        for stage, enabled in ordered:
            if not enabled:
                items.append(PlannedStageItem(stage=stage, status=PlannedStageStatus.SKIPPED))
                continue
            status = PlannedStageStatus.READY if not ready_assigned else PlannedStageStatus.PENDING
            items.append(PlannedStageItem(stage=stage, status=status))
            ready_assigned = True
        return items

    @staticmethod
    def _normalize_intent(raw_intent: str, user_message: str) -> str:
        intent = (raw_intent or "").strip().lower()
        if intent in SUPPORTED_INTENTS:
            return intent

        text = user_message.strip()
        if not text:
            return INTENT_CLARIFY
        if PlannerAgent._looks_like_research_or_collection_request(text):
            return INTENT_CRAWL_ONLY
        if any(token in text for token in ["发布", "发出来", "发到小红书", "直接发"]):
            return INTENT_PUBLISH_POST
        if any(token in text for token in ["完整帖子", "完整的帖子", "完整笔记", "完整的笔记", "完整小红书", "全流程", "全链路"]):
            return INTENT_FULL_POST
        # "给我写一篇关于XX的文案" → 用户指定了主题，需要全流程（搜索→分析→选题→内容）
        if any(token in text for token in ["关于", "针对", "围绕"]) and any(
            token in text for token in ["写一篇", "写个", "写一", "给我写", "帮我写", "生成"]
        ):
            return INTENT_FULL_POST
        if any(token in text for token in ["选题", "话题", "题材", "方向"]) and not any(
            token in text for token in ["完整", "全文", "全链路", "全流程", "写出", "写", "生成"]
        ):
            return INTENT_TOPIC_ONLY
        if PlannerAgent._looks_like_full_generation_request(text):
            return INTENT_FULL_POST
        if any(token in text for token in ["文案", "稿子", "文稿", "正文", "写一篇", "帮我写"]):
            return INTENT_COPYWRITING_ONLY
        if any(token in text for token in ["搜", "找", "爬", "检索", "热帖", "高质量内容"]):
            return INTENT_CRAWL_ONLY
        if PlannerAgent._has_goal_hint(text) and PlannerAgent._has_topic_hint(text):
            return INTENT_FULL_POST
        if PlannerAgent._has_goal_hint(text) and PlannerAgent._has_topic_hint(text):
            return INTENT_CRAWL_ONLY
        if any(token in text for token in ["发布", "发到小红书", "帮我发", "直接发"]):
            return INTENT_PUBLISH_POST
        if any(token in text for token in ["完整帖子", "完整的帖子", "完整笔记", "完整的笔记", "完整的小红书", "整篇帖子", "一篇完整"]):
            return INTENT_FULL_POST
        if any(token in text for token in ["选题", "话题", "题材", "方向"]) and not any(
            token in text for token in ["完整", "全文", "全链路", "全流程", "写出", "生成"]
        ):
            return INTENT_TOPIC_ONLY
        if PlannerAgent._looks_like_full_generation_request(text):
            return INTENT_FULL_POST
        if any(token in text for token in ["文案", "稿子", "文稿", "正文", "写一篇"]):
            return INTENT_COPYWRITING_ONLY
        if any(token in text for token in ["搜", "找", "爬", "检索", "高质量内容", "热帖", "品牌"]):
            return INTENT_CRAWL_ONLY
        return INTENT_CLARIFY

    @staticmethod
    def _looks_like_research_or_collection_request(text: str) -> bool:
        if not text:
            return False
        research_words = [
            "整理",
            "汇总",
            "收集",
            "找",
            "搜索",
            "爬取",
            "返回",
            "给我",
            "给我看",
            "列出",
            "盘点",
            "分析一下",
        ]
        object_words = ["帖子", "热帖", "案例", "素材", "样本", "成功人士", "笔记", "内容"]
        write_words = ["写一篇", "生成一篇", "写成", "文案", "草稿", "完整帖子", "完整笔记"]
        if any(word in text for word in write_words):
            return False
        has_object = any(word in text for word in object_words)
        has_research_action = any(word in text for word in research_words)
        has_counted_source_request = bool(
            re.search(r"\d{1,2}\s*(?:个|篇|条|则)?[^，。；\n]{0,24}?(?:帖子|热帖|案例|素材|笔记)", text)
        )
        return has_object and (has_research_action or has_counted_source_request)

    @staticmethod
    def _missing_required_fields(user_message: str, plan: PipelinePlan) -> list[str]:
        text = user_message.strip()
        if not text:
            return ["topic", "goal"]

        if plan.intent == INTENT_CLARIFY:
            fields = plan.clarification_fields or PlannerAgent._detect_missing_fields(text, plan.intent)
            return PlannerAgent._filter_resolved_missing_fields(text, plan, fields) or []

        missing = PlannerAgent._detect_missing_fields(text, plan.intent)
        missing = PlannerAgent._filter_resolved_missing_fields(text, plan, missing)
        required = INTENT_REQUIRED_FIELDS.get(plan.intent, ["topic", "goal"])
        for field in required:
            if field in missing:
                continue
            if field == "topic" and not PlannerAgent._has_topic_hint(text, plan):
                missing.append(field)
            elif field == "audience" and not (plan.audience or PlannerAgent._infer_audience(text)):
                missing.append(field)
            elif field == "tone" and not (plan.tone or PlannerAgent._infer_tone(text)):
                missing.append(field)
            elif field in {"account", "schedule"}:
                missing.append(field)
        return PlannerAgent._normalize_keywords(missing, max_keywords=5)

    @staticmethod
    def _filter_resolved_missing_fields(
        text: str,
        plan: PipelinePlan,
        fields: list[str],
    ) -> list[str]:
        resolved: list[str] = []
        for field in fields:
            if field == "topic" and PlannerAgent._has_topic_hint(text, plan):
                continue
            if field == "goal" and PlannerAgent._has_goal_hint(text):
                continue
            if field == "audience" and (plan.audience or PlannerAgent._infer_audience(text)):
                continue
            if field == "tone" and (plan.tone or PlannerAgent._infer_tone(text)):
                continue
            resolved.append(field)
        return PlannerAgent._normalize_keywords(resolved, max_keywords=5)

    @staticmethod
    def _detect_missing_fields(user_message: str, intent: str) -> list[str]:
        text = user_message.strip()
        if not text:
            return ["topic", "goal"]

        missing: list[str] = []
        if not PlannerAgent._has_topic_hint(text):
            missing.append("topic")

        if intent == INTENT_CLARIFY:
            if not PlannerAgent._has_goal_hint(text):
                missing.append("goal")
            return PlannerAgent._normalize_keywords(missing, max_keywords=5)

        generic_phrases = ["帮我写", "帮我做", "生成一篇", "写一篇", "写个", "做个", "来一篇"]
        if any(text == phrase or text.startswith(phrase) for phrase in generic_phrases):
            if "topic" not in missing and not PlannerAgent._has_topic_hint(text):
                missing.append("topic")
            if intent == INTENT_COPYWRITING_ONLY and "goal" not in missing and not PlannerAgent._has_goal_hint(text):
                missing.append("goal")

        return PlannerAgent._normalize_keywords(missing, max_keywords=5)

    @staticmethod
    def _build_clarification_question(fields: list[str]) -> str:
        mapping = {
            "topic": "主题方向",
            "goal": "内容目标",
            "audience": "目标受众",
            "product": "产品或服务",
            "scenario": "使用场景",
            "account": "发布账号",
            "schedule": "发布时间",
            "tone": "内容风格",
        }
        readable = [mapping.get(field, field) for field in fields]
        joined = "、".join(readable)
        examples = PlannerAgent._clarification_examples(fields)
        return f"当前信息还不够，我需要你再补充一下{joined}。{examples}信息越完整，生成内容质量越高。"

    @staticmethod
    def _clarification_examples(fields: list[str]) -> str:
        examples: list[str] = []
        if "topic" in fields:
            examples.append("比如你想写护肤、求职、防晒、洗面奶推荐这类具体方向。")
        if "goal" in fields:
            examples.append("也请告诉我是想搜高质量内容、出选题、写文案，还是生成完整帖子。")
        if "audience" in fields:
            examples.append("如果有明确受众，比如男生、大学生、职场新人，也可以一起补充。")
        if "tone" in fields:
            examples.append("也可以告诉我是想走真实分享、种草推荐、测评对比还是避坑提醒。")
        if "account" in fields or "schedule" in fields:
            examples.append("如果你要发布，还需要补充发布账号，以及是现在发布还是定时发布。")
        return "".join(examples)

    @staticmethod
    def _has_topic_hint(text: str, plan: PipelinePlan | None = None) -> bool:
        if plan and (plan.topic_seed.strip() or plan.search_query.strip() or plan.search_keywords):
            return True
        if PlannerAgent._extract_topic_from_generation_request(text):
            return True
        if any(token in text for token in ["\u5de5\u4f5c", "\u6e20\u9053", "\u6c42\u804c", "\u62db\u8058"]):
            return True
        if any(token in text for token in TOPIC_HINTS):
            return True
        # "关于XX的" / "针对XX的" — 用户用自然语言指定了主题
        import re as _re
        if _re.search(r"(?:\u5173\u4e8e|\u9488\u5bf9|\u56f4\u7ed5)\s*.+?\s*(?:\u7684|\u64b0\u5199|\u6587\u6848|\u5e16\u5b50|\u7b14\u8bb0)", text):
            return True
        if _re.search(r"(?:\u5199|\u751f\u6210|\u6765|\u505a|\u51fa|\u51c6\u5907)\s*(?:\u4e00\u7bc7|1\u7bc7|\u4e00\u671f|\u4e00\u4e2a)?\s*.+?\s*(?:\u7684)?\s*(?:\u6587\u6848|\u5e16\u5b50|\u7b14\u8bb0|\u5185\u5bb9)", text):
            return True
        return False

    @staticmethod
    def _is_actionable_generation_request(text: str, plan: PipelinePlan | None = None) -> bool:
        return PlannerAgent._has_goal_hint(text) and PlannerAgent._has_topic_hint(text, plan)

    @staticmethod
    def _looks_like_full_generation_request(text: str) -> bool:
        if not PlannerAgent._is_actionable_generation_request(text):
            return False
        return bool(re.search(r"(?:帖子|笔记|内容|小红书)", text))

    @staticmethod
    def _has_goal_hint(text: str) -> bool:
        if re.search(r"(?:\u5199|\u751f\u6210|\u5e2e\u6211|\u505a\u4e00\u671f|\u6587\u6848|\u5e16\u5b50|\u7b14\u8bb0|\u5185\u5bb9)", text):
            return True
        return any(token in text for token in GOAL_HINTS)

    @staticmethod
    def _infer_audience(text: str) -> str:
        for token in AUDIENCE_HINTS:
            if token in text:
                return token
        return ""

    @staticmethod
    def _infer_tone(text: str) -> str:
        for token in TONE_HINTS:
            if token in text:
                if token == "避坑":
                    return "避坑提醒"
                if token == "测评":
                    return "测评对比"
                if token == "攻略":
                    return "教程攻略"
                if token == "推荐":
                    return "种草推荐"
                return token
        return ""

    @staticmethod
    def _infer_topic_seed(text: str, search_query: str) -> str:
        topic = PlannerAgent._extract_topic_from_generation_request(text)
        if topic:
            return topic
        for token in TOPIC_HINTS:
            if token in text:
                return token
        return search_query.strip()

    @staticmethod
    def _extract_topic_from_generation_request(text: str) -> str:
        match = re.search(
            r"(?:\u5199|\u751f\u6210|\u6765|\u505a|\u51fa|\u51c6\u5907)\s*(?:\u4e00\u7bc7|1\u7bc7|\u4e00\u671f|\u4e00\u4e2a)?\s*(.+?)\s*(?:\u7684)?\s*(?:\u6587\u6848|\u5e16\u5b50|\u7b14\u8bb0|\u5185\u5bb9)",
            text.strip(),
        )
        if not match:
            return ""
        topic = match.group(1).strip(" ，。；;、")
        return topic[:30]

    @staticmethod
    def _normalize_keywords(keywords: list[str], max_keywords: int = 5) -> list[str]:
        cleaned: list[str] = []
        for item in keywords:
            token = str(item).strip()
            if not token or token in cleaned:
                continue
            cleaned.append(token[:20])
            if len(cleaned) >= max_keywords:
                break
        return cleaned

    @staticmethod
    def _extract_keywords_rule_based(text: str, max_keywords: int = 5) -> list[str]:
        text = text.strip()
        if not text:
            return []
        if "考研" in text and ("中科大" in text or "中国科学技术大学" in text):
            year_prefix = "26" if "26" in text or "2026" in text else ""
            return PlannerAgent._normalize_keywords(
                [f"{year_prefix}考研上岸中科大", "中科大考研经验贴"],
                max_keywords=max_keywords,
            )
        separators = r"[，。；;、|\n]+|(?:以及|还有|和|跟|并且|同时|再加上)"
        candidates = [part.strip() for part in re.split(separators, text) if part.strip()]
        keywords: list[str] = []
        for candidate in candidates:
            candidate = re.sub(r"^(帮我|想找|我想找|想查|我想查|请找|请查|查一下|搜一下|搜一个|搜索|检索|分析|关于|看看)", "", candidate).strip()
            candidate = re.sub(r"(相关.*|的小红书.*|的小红书帖子.*|的小红书热帖.*|的经验贴.*|的经验帖.*|的帖子.*|热帖.*)$", "", candidate).strip()
            if len(candidate) >= 2:
                keywords.append(candidate)
        return PlannerAgent._normalize_keywords(keywords or [text[:20]], max_keywords=max_keywords)
