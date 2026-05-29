from __future__ import annotations

import re
import time
import uuid

from agents.content_agent import ContentAgent
from agents.crawler_agent import CrawlerAgent
from core.agent_loop import AgentLoopEngine, LoopHooks
from models.prompts import REVISION_PROMPT, SEARCH_KEYWORD_PROMPT, TASK_ROUTING_PROMPT
from models.schemas import (
    AgentRunRequest,
    AgentRunResponse,
    AnalysisResult,
    AnalyzeRequest,
    AnalyzeResponse,
    ContentItem,
    ContentGenerateRequest,
    ContentGenerateResponse,
    DraftPackage,
    GeneratedTopicWithContents,
    NoteItem,
    PipelineState,
    PlannedStageItem,
    TaskRoutingDecision,
    TopicItem,
    TopicGenerateRequest,
    TopicGenerateResponse,
)
from models.states import PipelineStage, PlannedStageStatus

from api.dependencies import AppContainer
from services.trace_service import begin_span, set_trace_id


EXPLICIT_CRAWL_TERMS = (
    "\u627e\u7d20\u6750",
    "\u53c2\u8003\u70ed\u5e16",
    "\u53c2\u8003\u7206\u6b3e",
    "\u7206\u6b3e\u5206\u6790",
    "\u91c7\u96c6",
    "\u722c\u53d6",
    "\u70ed\u5e16",
    "\u5c0f\u7ea2\u4e66\u7d20\u6750",
)


class AgentRunHooks(LoopHooks):
    max_stage_retries: int = 1
    max_content_regenerations: int = 0

    def should_skip_failed_stage(self, state: PipelineState, stage: PipelineStage) -> bool:
        if super().should_skip_failed_stage(state, stage):
            return True
        if stage != PipelineStage.CRAWLING:
            return False
        raw_errors = " ".join(
            [
                str(state.error_message or ""),
                " ".join(str(item) for item in state.metadata.get("crawler_errors", [])),
                str(state.metadata.get("crawler_error", "")),
            ]
        ).lower()
        if "xiaohongshu_login_expired" in raw_errors or "xiaohongshu_login_required" in raw_errors:
            return False
        route = state.metadata.get("delivery_route")
        deliverable = route.get("final_deliverable") if isinstance(route, dict) else ""
        if deliverable == "source_notes" or not state.plan.needs_content_generation:
            return False
        topic = state.plan.topic_seed or state.search_query or state.user_message
        state.input_notes = _fallback_notes_for_content_generation(topic)
        state.metadata["crawl_fallback_used"] = True
        state.metadata["crawl_fallback_reason"] = state.error_message or "crawler_failed"
        return True


def _fallback_notes_for_content_generation(topic: str) -> list[NoteItem]:
    clean_topic = (topic or "小红书内容").strip()
    return [
        NoteItem(
            title=f"{clean_topic}：新手最容易卡住的 3 个问题",
            content=(
                f"围绕“{clean_topic}”做内容时，先把人群、痛点和可执行步骤讲清楚。"
                "开头直接点出困惑，中段拆成方法清单，结尾给出行动建议和评论互动。"
            ),
            likes=320,
            favorites=180,
            comments=36,
            tags=[clean_topic, "学习路线", "经验分享"],
            keyword_used=clean_topic,
        ),
        NoteItem(
            title=f"普通人怎么开始做{clean_topic}？一篇讲明白",
            content=(
                "高质量帖子通常不是堆概念，而是用具体场景承接读者问题。"
                "可以按“为什么重要、先学什么、怎么练、怎么避坑”的结构展开。"
            ),
            likes=260,
            favorites=150,
            comments=24,
            tags=[clean_topic, "入门指南", "避坑"],
            keyword_used=clean_topic,
        ),
        NoteItem(
            title=f"{clean_topic}学习路线：从概念到实操",
            content=(
                "正文适合使用阶段式路线：基础概念、工具实践、项目复盘、持续优化。"
                "每一段都配一个具体动作，让读者读完知道下一步做什么。"
            ),
            likes=410,
            favorites=230,
            comments=42,
            tags=[clean_topic, "路线图", "实操"],
            keyword_used=clean_topic,
        ),
    ]


def _normalize_run_mode(value: str | None) -> str:
    return "deep" if value == "deep" else "fast"


def _has_explicit_crawl_request(user_message: str) -> bool:
    text = user_message or ""
    return any(term in text for term in EXPLICIT_CRAWL_TERMS)


def _has_generation_request(user_message: str) -> bool:
    text = user_message or ""
    write_terms = (
        "\u5199",
        "\u751f\u6210",
        "\u521b\u4f5c",
        "\u6539\u5199",
        "\u4eff\u5199",
        "\u4ea7\u51fa",
        "\u64b0\u5199",
        "\u6587\u6848",
        "\u6b63\u6587",
        "\u8349\u7a3f",
    )
    content_terms = (
        "\u5e16\u5b50",
        "\u7b14\u8bb0",
        "\u5185\u5bb9",
        "\u5c0f\u7ea2\u4e66",
        "\u63a8\u6587",
    )
    return any(term in text for term in write_terms) and any(term in text for term in content_terms)


def _has_source_only_request(user_message: str) -> bool:
    text = user_message or ""
    source_terms = (
        "\u7d20\u6750",
        "\u70ed\u5e16",
        "\u7206\u6587",
        "\u6848\u4f8b",
        "\u7b14\u8bb0",
    )
    collect_terms = (
        "\u627e",
        "\u641c",
        "\u641c\u7d22",
        "\u91c7\u96c6",
        "\u722c\u53d6",
        "\u6574\u7406",
        "\u5217\u51fa",
    )
    return any(term in text for term in source_terms) and any(term in text for term in collect_terms)


def _apply_mode_defaults(state: PipelineState) -> None:
    state.mode = _normalize_run_mode(state.mode)
    state.metadata["mode"] = state.mode
    state.metadata["workflow_mode"] = state.mode

    if state.mode == "deep":
        state.raw_crawl_limit = max(state.raw_crawl_limit, 30)
        state.final_note_limit = max(state.final_note_limit, 8)
        state.min_final_note_count = max(state.min_final_note_count, 3)
        state.topic_count = max(state.topic_count, 3)
        state.content_count_per_topic = max(1, state.content_count_per_topic)
        state.review_threshold = max(state.review_threshold, 75.0)
        state.max_reflections = max(1, min(state.max_reflections or 1, 2))
        return

    state.raw_crawl_limit = max(6, min(state.raw_crawl_limit, 12))
    state.final_note_limit = max(1, min(state.final_note_limit, 3))
    state.min_final_note_count = 1
    state.topic_count = max(1, min(state.topic_count, 2))
    state.content_count_per_topic = 1
    state.review_threshold = min(state.review_threshold, 65.0)
    state.max_reflections = max(0, min(state.max_reflections, 1))


def _is_source_note_route(state: PipelineState) -> bool:
    route = state.metadata.get("delivery_route")
    deliverable = route.get("final_deliverable") if isinstance(route, dict) else ""
    return deliverable == "source_notes" or state.plan.intent == "crawl_only"


def _maybe_convert_source_note_crawl_failure(state: PipelineState) -> None:
    if not state.failed or not _is_source_note_route(state):
        return
    if state.input_notes:
        return

    raw_errors = " ".join(
        [
            str(state.error_message or ""),
            " ".join(str(item) for item in state.metadata.get("crawler_errors", [])),
            str(state.metadata.get("crawler_error", "")),
        ]
    ).lower()
    markers = [
        "xiaohongshu_login_expired",
        "xiaohongshu_login_required",
        "no search cards",
        "login state",
        "not collect enough",
        "rate-limiting",
    ]
    if not any(marker in raw_errors for marker in markers):
        return

    original_error = state.error_message
    state.failed = False
    state.error_message = None
    state.stage = PipelineStage.WAITING_FOR_INPUT
    state.plan.needs_clarification = True
    state.plan.clarification_fields = ["xhs_login"]
    state.plan.clarification_question = (
        "这次没有采集到小红书搜索结果。当前登录态可能已过期，或者小红书临时限制了搜索。"
        "请先点击“登录小红书”完成扫码/登录，然后重新发送这条找素材需求。"
    )
    state.plan.clarification_tips = (
        "如果登录后仍然没有结果，可以把关键词放宽一点，例如把“2026年11408考研上岸”改成"
        "“2026考研 408 上岸经验”或“408考研经验贴”。"
    )
    state.metadata["crawl_failure_recovered"] = True
    state.metadata["crawl_failure_original_error"] = original_error


def _maybe_convert_xhs_login_failure(state: PipelineState) -> None:
    if not state.failed:
        return
    raw_errors = " ".join(
        [
            str(state.error_message or ""),
            " ".join(str(item) for item in state.metadata.get("crawler_errors", [])),
            str(state.metadata.get("crawler_error", "")),
        ]
    ).lower()
    if "xiaohongshu_login_expired" not in raw_errors and "xiaohongshu_login_required" not in raw_errors:
        return
    original_error = state.error_message
    _mark_xhs_login_required(state)
    state.metadata["xhs_login_failure_recovered"] = True
    state.metadata["xhs_login_failure_original_error"] = original_error


def _mark_xhs_login_required(state: PipelineState) -> None:
    state.failed = False
    state.error_message = None
    state.stage = PipelineStage.WAITING_FOR_INPUT
    state.plan.needs_clarification = True
    state.plan.clarification_fields = ["xhs_login"]
    state.plan.clarification_question = (
        "您暂时没有登录小红书，请先登录。"
        "请点击输入框下方的“登录小红书”，在弹出的浏览器里完成扫码登录，"
        "登录完成后再重新发送这条找素材需求。"
    )
    state.plan.clarification_tips = (
        "登录后建议把关键词写成更容易被小红书搜到的形式，例如："
        "“2026考研 408 上岸经验”或“408考研经验贴”。"
    )
    state.metadata["xhs_login_required"] = True


def _build_default_user_message(request: AgentRunRequest) -> str:
    note_count = len(request.candidate_notes or request.items or [])
    return (
        f"请基于当前输入帮我完成小红书内容分析与生成。"
        f"受众：{request.audience}；语气：{request.tone}；"
        f"目标：生成 {request.topic_count} 个选题，每个选题 {request.content_count_per_topic} 条内容；"
        f"当前样本数：{note_count}。"
    )


def _normalize_keywords(keywords: list[str], max_keywords: int = 5) -> list[str]:
    cleaned: list[str] = []
    for item in keywords:
        token = item.strip()
        if not token or token in cleaned:
            continue
        cleaned.append(token)
        if len(cleaned) >= max_keywords:
            break
    return cleaned


def _extract_keywords_rule_based(text: str, max_keywords: int = 5) -> list[str]:
    separators = r"[，,。；;、/\|\n]+|(?:以及|还有|和|跟|并且|同时|再加上)"
    candidates = [part.strip() for part in re.split(separators, text) if part.strip()]
    keywords: list[str] = []
    for candidate in candidates:
        candidate = re.sub(r"^(帮我|想找|我想找|想查|我想查|请找|请查|查一下|搜一下|搜一个|搜索|检索|分析|关于|看看)", "", candidate).strip()
        candidate = re.sub(r"(相关.*|的小红书.*|的小红书帖子.*|的小红书热帖.*|的经验贴.*|的经验帖.*|的帖子.*|热帖.*)$", "", candidate).strip()
        if len(candidate) < 2:
            continue
        keywords.append(candidate[:16])
    normalized = _normalize_keywords(keywords, max_keywords=max_keywords)
    return normalized or _normalize_keywords([text.strip()[:16]], max_keywords=max_keywords)


def _fallback_delivery_route(user_message: str) -> dict:
    text = user_message.strip()
    count = _requested_source_note_count(text) or 0
    if _has_generation_request(text):
        publish_requested = any(term in text for term in ("\u53d1\u5e03", "\u53d1\u51fa\u53bb", "\u76f4\u63a5\u53d1"))
        return {
            "final_deliverable": "publish_ready_content" if publish_requested else "content_draft",
            "intent": "publish_post" if publish_requested else "full_post",
            "topic": text,
            "count": 0,
            "confidence": 0.9,
            "reason": "explicit_generation_request",
            "source": "rules",
        }
    if _has_source_only_request(text):
        return {
            "final_deliverable": "source_notes",
            "intent": "crawl_only",
            "topic": text,
            "count": count,
            "confidence": 0.88,
            "reason": "explicit_source_collection_request",
            "source": "rules",
        }

    source_word = r"(帖子|热帖|爆文|案例|素材|笔记|小红书内容)"
    collect_word = r"(整理|找|搜索|搜|返回|收集|爬取|给我|列出|看看)"
    write_word = r"(写|生成|创作|改写|仿写|产出|撰写|发一篇|写一篇|文案|正文|草稿)"
    topic_word = r"(选题|标题|方向|话题|题目)"
    analysis_word = r"(分析|趋势|误区|关键词|洞察|报告|总结|复盘|为什么)"

    if re.search(r"(发布|发出去|直接发)", text) and re.search(write_word, text):
        return {
            "final_deliverable": "publish_ready_content",
            "intent": "publish_post",
            "topic": text,
            "count": 0,
            "confidence": 0.88,
            "reason": "用户要求生成并发布。",
            "source": "rules",
        }
    if re.search(write_word, text):
        return {
            "final_deliverable": "content_draft",
            "intent": "full_post",
            "topic": text,
            "count": 0,
            "confidence": 0.86,
            "reason": "用户明确要求写作或生成文案，素材采集只是前置参考步骤。",
            "source": "rules",
        }
    if re.search(collect_word, text) and re.search(source_word, text):
        return {
            "final_deliverable": "source_notes",
            "intent": "crawl_only",
            "topic": re.sub(r"^(给我|帮我|请|请帮我|整理|找|搜索|搜|返回|收集|爬取)+", "", text).strip(),
            "count": count,
            "confidence": 0.86,
            "reason": "用户要求整理或返回已有帖子素材。",
            "source": "rules",
        }
    if count and re.search(source_word, text) and not re.search(write_word, text):
        return {
            "final_deliverable": "source_notes",
            "intent": "crawl_only",
            "topic": text,
            "count": count,
            "confidence": 0.84,
            "reason": "用户给出数量并要求帖子/热帖，最终交付物是素材列表。",
            "source": "rules",
        }
    if re.search(topic_word, text) and not re.search(write_word, text):
        return {
            "final_deliverable": "topic_list",
            "intent": "topic_only",
            "topic": text,
            "count": 0,
            "confidence": 0.78,
            "reason": "用户主要要选题或标题方向。",
            "source": "rules",
        }
    if re.search(analysis_word, text) and not re.search(write_word, text):
        return {
            "final_deliverable": "analysis_report",
            "intent": "analysis_only",
            "topic": text,
            "count": 0,
            "confidence": 0.76,
            "reason": "用户主要要分析而不是成稿。",
            "source": "rules",
        }
    return {
        "final_deliverable": "content_draft",
        "intent": "full_post",
        "topic": text,
        "count": 0,
        "confidence": 0.55,
        "reason": "未发现明确素材整理或分析诉求，按默认内容生成处理。",
        "source": "rules",
    }


def _normalize_delivery_route(payload: dict, user_message: str) -> dict:
    fallback = _fallback_delivery_route(user_message)
    if not isinstance(payload, dict):
        return fallback

    allowed_deliverables = {
        "source_notes",
        "topic_list",
        "analysis_report",
        "content_draft",
        "publish_ready_content",
        "clarification",
    }
    deliverable = str(payload.get("final_deliverable") or "").strip()
    if deliverable not in allowed_deliverables:
        deliverable = fallback["final_deliverable"]

    intent_by_deliverable = {
        "source_notes": "crawl_only",
        "topic_list": "topic_only",
        "analysis_report": "analysis_only",
        "content_draft": "full_post",
        "publish_ready_content": "publish_post",
        "clarification": "clarify_request",
    }
    raw_intent = str(payload.get("intent") or "").strip()
    intent = intent_by_deliverable.get(deliverable, fallback["intent"])
    if raw_intent in {"crawl_only", "topic_only", "full_post", "publish_post", "clarify_request"}:
        intent = raw_intent
    if deliverable == "source_notes":
        intent = "crawl_only"
    elif deliverable == "analysis_report":
        intent = "analysis_only"

    try:
        count = int(payload.get("count") or fallback.get("count") or 0)
    except (TypeError, ValueError):
        count = int(fallback.get("count") or 0)
    if deliverable == "source_notes" and not count:
        count = int(fallback.get("count") or 0)

    try:
        confidence = float(payload.get("confidence") or fallback.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = float(fallback.get("confidence") or 0)
    if fallback["final_deliverable"] == "source_notes" and deliverable != "source_notes":
        # Rule certainty beats a vague LLM guess for counted hot-post/source requests.
        return fallback
    if (
        fallback["final_deliverable"] in {"content_draft", "publish_ready_content"}
        and deliverable == "source_notes"
        and float(fallback.get("confidence") or 0) >= 0.8
    ):
        # Explicit writing/generation requests should not be reduced to material collection.
        return fallback

    return {
        "final_deliverable": deliverable,
        "intent": intent,
        "topic": str(payload.get("topic") or fallback.get("topic") or user_message).strip(),
        "count": max(0, min(count, 50)),
        "confidence": max(0.0, min(confidence, 1.0)),
        "reason": str(payload.get("reason") or fallback.get("reason") or "").strip(),
        "source": str(payload.get("source") or "llm").strip(),
    }


async def _resolve_delivery_route(container: AppContainer, state: PipelineState) -> dict:
    span = begin_span(
        "task_routing",
        "resolve_delivery_route",
        input_summary={
            "mode": state.mode,
            "user_message_chars": len(state.user_message),
            "has_active_generation": bool(state.metadata.get("active_generation")),
            "has_active_task": bool(state.metadata.get("active_task")),
        },
    )
    fallback = _fallback_delivery_route(state.user_message)
    if fallback.get("final_deliverable") == "source_notes" and float(fallback.get("confidence") or 0) >= 0.8:
        span.end(output_summary={**fallback, "source": fallback.get("source") or "fallback_rule"})
        return fallback
    if not getattr(container.llm_service, "enabled", False):
        span.end(output_summary={**fallback, "source": fallback.get("source") or "fallback"})
        return fallback

    context = _routing_context(
        state.metadata.get("active_generation"),
        None,
        state.metadata.get("active_task"),
    )
    user = DELIVERABLE_ROUTE_USER_TEMPLATE.format(
        user_message=state.user_message,
        routing_context=context,
    )
    try:
        result = await container.llm_service.chat_json(system=DELIVERABLE_ROUTE_SYSTEM, user=user)
        parsed = container.llm_service.extract_json(result.content) or {}
    except Exception as exc:  # noqa: BLE001
        span.end(status="failed", output_summary={"fallback": fallback}, error=str(exc))
        return fallback
    route = _normalize_delivery_route(parsed, state.user_message)
    span.end(
        output_summary={
            "final_deliverable": route.get("final_deliverable"),
            "intent": route.get("intent"),
            "confidence": route.get("confidence"),
            "source": route.get("source"),
            "mode": state.mode,
        }
    )
    return route


async def _resolve_search_intent(container: AppContainer, request: AgentRunRequest) -> tuple[str, list[str]]:
    search_query = request.search_query.strip() or request.user_message.strip() or _build_default_user_message(request)

    explicit_keywords = _normalize_keywords(request.search_keywords)
    if explicit_keywords:
        return search_query, explicit_keywords

    if container.llm_service.enabled and request.user_message.strip():
        system = SEARCH_KEYWORD_PROMPT.system
        user = SEARCH_KEYWORD_PROMPT.render_user(user_message=request.user_message.strip())
        result = await container.llm_service.chat_json(system=system, user=user)
        parsed = container.llm_service.extract_json(result.content) or {}
        keywords = parsed.get("search_keywords", [])
        if isinstance(keywords, list):
            resolved = _normalize_keywords([str(item) for item in keywords])
            if resolved:
                parsed_query = str(parsed.get("search_query") or search_query).strip()
                return parsed_query, resolved

    return search_query, _extract_keywords_rule_based(search_query)


ROUTING_ACTIONS = {
    "new_task",
    "answer_pending_clarification",
    "revise_active_generation",
    "select_candidate",
    "confirm_active_generation",
    "confirm_publish",
    "abandon_active_generation",
    "abandon_task",
    "ask_clarification",
}


ACTION_THRESHOLDS = {
    "confirm_publish": 0.90,
    "abandon_task": 0.85,
    "abandon_active_generation": 0.85,
    "new_task": 0.80,
    "confirm_active_generation": 0.75,
    "revise_active_generation": 0.70,
    "select_candidate": 0.65,
    "answer_pending_clarification": 0.60,
    "ask_clarification": 0.0,
}

DELIVERABLE_ROUTE_SYSTEM = """
你是小红书 Agent 的任务交付物识别器。你只判断用户最终想拿到什么，不负责写作。

必须从以下 final_deliverable 中选择一个：
- source_notes：用户要找、整理、返回、收集、爬取已有帖子/热帖/案例/素材/笔记。
- topic_list：用户要选题、标题、内容方向，不要求完整正文。
- analysis_report：用户要分析趋势、误区、关键词、竞品、现象，不要求完整正文。
- content_draft：用户要写文案、写帖子、生成笔记、改写成小红书正文。
- publish_ready_content：用户明确要求写完并发布。
- clarification：无法判断用户到底要什么。

关键规则：
1. “给我/整理/找/返回/收集/爬取 + N个/篇 + 帖子/热帖/案例/素材/笔记”一定是 source_notes。
2. 只有出现“写/生成/改写/仿写/产出文案/发一篇”等创作动作，才是 content_draft。
3. “热帖”默认是已有内容素材，不是让你写热帖。
4. 不要因为系统是内容生成 Agent 就默认写文案。

只输出 JSON，不要解释。
"""

DELIVERABLE_ROUTE_USER_TEMPLATE = """
用户输入：
{user_message}

当前会话上下文：
{routing_context}

输出 JSON：
{{
  "final_deliverable": "source_notes | topic_list | analysis_report | content_draft | publish_ready_content | clarification",
  "intent": "crawl_only | topic_only | analysis_only | full_post | publish_post | clarify_request",
  "topic": "提炼后的主题",
  "count": 0,
  "confidence": 0.0,
  "reason": "一句话说明为什么这样路由"
}}
"""


PENDING_NEW_TASK_HINTS = [
    "换个主题",
    "新任务",
    "另外",
    "接下来",
    "重新开始",
    "重新生成一个",
    "再帮我写一篇",
    "不要这个了",
    "算了",
]


def _looks_like_new_task_while_pending(user_message: str) -> bool:
    text = user_message.strip()
    if not text:
        return False
    return any(hint in text for hint in PENDING_NEW_TASK_HINTS)


def _merge_pending_user_message(pending_task: dict, user_message: str) -> str:
    previous = str(pending_task.get("user_message") or "").strip()
    addition = user_message.strip()
    if not previous:
        return addition
    if not addition:
        return previous
    return f"{previous}\n\n[用户补充信息]\n{addition}"


def _pending_task_payload(state: PipelineState, resume_mode: str = "replan") -> dict:
    return {
        "type": "clarification",
        "resume_mode": resume_mode,
        "run_id": state.run_id,
        "session_id": state.session_id,
        "task_id": state.task_id,
        "user_message": state.user_message,
        "clarification_question": state.plan.clarification_question,
        "clarification_fields": state.plan.clarification_fields,
        "pipeline_state": state.model_dump(),
        "created_at": time.time(),
    }


def _material_pending_task_payload(state: PipelineState, content_mode: str) -> dict:
    payload = _pending_task_payload(state, resume_mode="material_context")
    payload["type"] = "material_clarification"
    payload["content_mode"] = content_mode
    return payload


def _is_skip_material_reply(user_message: str) -> bool:
    text = user_message.strip().lower()
    if not text:
        return False
    skip_words = [
        "没有",
        "没素材",
        "没有素材",
        "没经历",
        "没有经历",
        "跳过",
        "不用",
        "不提供",
        "直接写",
        "你写",
        "你发挥",
        "随便",
        "先写",
        "无",
        "no",
        "skip",
    ]
    return any(word in text for word in skip_words)


def _material_question_for(content_mode: str, topic: str) -> str:
    if content_mode == "review_recommendation":
        return (
            f"这类「{topic}」内容最好有真实体验支撑。你用过/去过/体验过吗？\n\n"
            "可以简单补充 2-3 个点：\n"
            "1. 你真实体验过的对象是什么？\n"
            "2. 最满意或最不满意的一点是什么？\n"
            "3. 有没有具体价格、时间、场景或对比？\n\n"
            "如果没有真实体验，直接回复“没有素材”或“跳过”，我会改成客观整理/避坑指南，不编造亲测经历。"
        )
    return (
        f"这类「{topic}」内容如果写成亲身经历，需要你的真实素材，不然容易变成编造故事。\n\n"
        "你可以随便补充几句：\n"
        "1. 这件事大概发生在什么时候？\n"
        "2. 你经历了什么关键转折或踩坑？\n"
        "3. 最后结果怎么样？有没有具体数字？\n\n"
        "如果没有真实经历，直接回复“没有素材”或“跳过”，我会改成方法论/观察分析角度来写，不编造第一人称经历。"
    )


def _should_ask_for_material(state: PipelineState, content_mode: str) -> bool:
    if state.plan.intent in {"crawl_only", "topic_only"}:
        return False
    if content_mode not in {"personal_experience", "review_recommendation"}:
        return False
    if state.metadata.get("material_clarification_handled"):
        return False
    if state.metadata.get("material_clarification_skipped"):
        return False
    return not state.metadata.get("has_user_material")


def _requested_source_note_count(user_message: str) -> int | None:
    match = re.search(r"(\d{1,2})\s*(?:个|篇|条|则)?[^，。；\n]{0,24}?(?:帖子|热帖|案例|素材|笔记)", user_message)
    if not match:
        return None
    try:
        return max(1, min(int(match.group(1)), 30))
    except ValueError:
        return None


def _clean_source_note_search_topic(text: str) -> str:
    topic = text.strip()
    if not topic:
        return ""
    topic = re.sub(r"^(给我|帮我|请|请帮我|整理|找|搜索|搜|返回|收集|爬取|列出|看看)+", "", topic).strip()
    topic = re.sub(r"^\d{1,2}\s*(?:个|篇|条|则)?", "", topic).strip()
    topic = re.sub(r"(?:的)?(?:小红书)?(?:帖子|热帖|爆文|案例|素材|笔记|内容)$", "", topic).strip()
    topic = re.sub(r"(?:的)?(?:高质量|优质|高赞|热门|爆款)$", "", topic).strip()
    topic = re.sub(r"^学习(?=.+(?:微调|后训练|大模型|llm|LLM))", "", topic).strip()
    topic = topic.strip(" 的，。；;、")
    return topic or text.strip()


def _task_type_from_plan(plan: PipelinePlan) -> str:
    if plan.intent == "crawl_only":
        return "source_collection"
    if plan.intent == "topic_only":
        return "topic_research"
    if plan.intent in {"copywriting_only", "full_post", "publish_post"}:
        return "copywriting"
    if plan.needs_content_generation:
        return "copywriting"
    if plan.needs_topic_generation:
        return "topic_research"
    if plan.needs_crawl or plan.needs_analysis:
        return "source_collection"
    return plan.intent or "unknown"


def _active_task_payload(
    state: PipelineState,
    *,
    candidates: list[dict] | None = None,
    draft_package: DraftPackage | None = None,
    publish_requested: bool = False,
) -> dict:
    task_type = _task_type_from_plan(state.plan)
    source_notes = [note.model_dump() for note in state.input_notes]
    payload = {
        "parent_run_id": state.run_id,
        "session_id": state.session_id,
        "task_id": state.task_id,
        "task_type": task_type,
        "mode": state.mode,
        "status": "awaiting_publish_confirmation" if publish_requested else "completed",
        "query": state.search_query or state.user_message,
        "user_message": state.user_message,
        "search_keywords": state.search_keywords,
        "source_notes": source_notes,
        "source_note_count": len(source_notes),
        "analysis": state.analysis.model_dump() if state.analysis else None,
        "topics": [topic.model_dump() for topic in state.topics],
        "candidates": candidates or [],
        "draft_package": draft_package.model_dump() if draft_package else None,
        "selected_note_ids": [],
        "selected_index": None,
        "publish_requested": publish_requested,
        "updated_at": time.time(),
    }
    if candidates:
        payload["status"] = "awaiting_publish_confirmation" if publish_requested else "awaiting_selection"
        payload["draft_count"] = len(candidates)
    return payload


def _routing_context(
    active_generation: dict | None,
    pending_task: dict | None = None,
    active_task: dict | None = None,
) -> dict:
    candidates = _active_generation_candidates(active_generation) if active_generation else []
    status = str(active_generation.get("status") or "") if active_generation else ""
    pending_question = str(pending_task.get("clarification_question") or "") if pending_task else ""
    return {
        "has_active_generation": active_generation is not None,
        "has_active_task": active_task is not None,
        "active_task_type": str(active_task.get("task_type") or "") if active_task else "",
        "active_source_note_count": int(active_task.get("source_note_count") or 0) if active_task else 0,
        "has_pending_task": pending_task is not None,
        "pending_question": pending_question,
        "last_system_action": status or ("waiting_for_clarification" if pending_task else ""),
        "candidate_count": len(candidates),
        "publish_requested": bool(active_generation and active_generation.get("publish_requested")),
        "active_generation_summary": _active_generation_summary(active_generation),
        "active_task_summary": _active_task_summary(active_task),
    }


def _active_task_summary(active_task: dict | None) -> str:
    if not active_task:
        return "无"
    lines = [
        f"任务类型：{active_task.get('task_type') or '未知'}",
        f"状态：{active_task.get('status') or '未知'}",
        f"查询：{active_task.get('query') or '未知'}",
        f"素材数：{active_task.get('source_note_count', 0)}",
        f"草稿数：{active_task.get('draft_count', len(active_task.get('candidates') or []))}",
    ]
    return "\n".join(lines)


def _should_execute_route(action: str, confidence: float) -> bool:
    return confidence >= ACTION_THRESHOLDS.get(action, 0.75)


def _route_clarification_question(action: str, context: dict) -> str:
    if action == "confirm_publish":
        return "你是想发布当前草稿吗？请明确回复“发布第1篇”或“先不发布”。"
    if action in {"new_task", "abandon_task", "abandon_active_generation"}:
        return "你是想开启新任务，还是继续当前任务？"
    if context.get("has_active_generation"):
        return "你是想修改当前草稿、确认使用，还是开启新任务？"
    if context.get("has_pending_task"):
        return "你是在回答上一轮的问题，还是想开启新任务？"
    return "我还不确定你的意图，可以再说明一下你想让我做什么吗？"


def _legacy_active_generation_summary(active_generation: dict | None) -> str:
    if not active_generation:
        return "无"
    topic = active_generation.get("topic_title", "")
    title = active_generation.get("content_title", "")
    score = active_generation.get("score", "")
    revision_count = active_generation.get("revision_count", 0)
    return (
        f"主题：{topic or '未知'}\n"
        f"当前标题：{title or '未知'}\n"
        f"当前评分：{score if score != '' else '未知'}\n"
        f"修订次数：{revision_count}"
    )


def _route_by_rules(user_message: str, context: dict) -> TaskRoutingDecision | None:
    text = user_message.strip()
    if not text:
        return None

    if _looks_like_fresh_task_request(text):
        return TaskRoutingDecision(
            action="new_task",
            confidence=0.92,
            reason="matched_fresh_task_request",
            should_start_new_task=True,
            source="rule",
            requires_replan=True,
        )

    candidate_index = _candidate_index_from_feedback(text)
    if (
        context["has_active_generation"]
        and context["candidate_count"] > 0
        and ("发布" in text or "确认发布" in text)
    ):
        return TaskRoutingDecision(
            action="confirm_publish",
            confidence=0.92,
            reason="matched_explicit_publish_confirmation",
            should_start_new_task=False,
            source="rule",
            selected_index=candidate_index,
            requires_replan=False,
        )
    if candidate_index is not None and context["candidate_count"] > 0:
        if any(word in text for word in ["改", "润", "优化", "重写", "换", "调整"]):
            return TaskRoutingDecision(
                action="revise_active_generation",
                confidence=0.92,
                reason="matched_candidate_revision",
                should_start_new_task=False,
                source="rule",
                selected_index=candidate_index,
                requires_replan=False,
            )
        if any(word in text for word in ["可以", "确认", "就用", "满意"]):
            return TaskRoutingDecision(
                action="confirm_active_generation",
                confidence=0.9,
                reason="matched_candidate_confirmation",
                should_start_new_task=False,
                source="rule",
                selected_index=candidate_index,
                requires_replan=False,
            )
        return TaskRoutingDecision(
            action="select_candidate",
            confidence=1.0,
            reason="matched_candidate_selection",
            should_start_new_task=False,
            source="rule",
            selected_index=candidate_index,
            requires_replan=False,
        )

    confirm_words = ["这版可以", "就用这个", "确认", "不用改了", "满意", "可以发布", "保存最终版"]
    abandon_words = ["算了", "不用了", "不要了", "取消", "先不做了"]
    revise_words = ["改", "修改", "重写", "再真实", "标题", "正文", "换个语气", "口语", "不太行", "再来一版"]
    new_task_words = ["换个主题", "新任务", "另外", "接下来", "重新生成一个", "再帮我写一篇"]
    publish_words = ["发", "发布", "冲", "安排", "就这个发", "可以发"]

    if text in publish_words and context["publish_requested"]:
        return TaskRoutingDecision(
            action="confirm_publish",
            confidence=1.0,
            reason="matched_publish_confirmation",
            should_start_new_task=False,
            source="rule",
            requires_replan=False,
        )
    if any(word in text for word in confirm_words):
        return TaskRoutingDecision(
            action="confirm_active_generation",
            confidence=0.95,
            reason="matched_confirm_phrase",
            should_start_new_task=False,
            source="rule",
            requires_replan=False,
        )
    if any(word in text for word in abandon_words):
        return TaskRoutingDecision(
            action="abandon_task",
            confidence=0.9,
            reason="matched_abandon_phrase",
            should_start_new_task=False,
            source="rule",
            requires_replan=False,
        )
    if any(word in text for word in new_task_words):
        return TaskRoutingDecision(
            action="new_task",
            confidence=0.85,
            reason="matched_new_task_phrase",
            should_start_new_task=True,
            source="rule",
            requires_replan=True,
        )
    if any(word in text for word in revise_words) and context["has_active_generation"]:
        return TaskRoutingDecision(
            action="revise_active_generation",
            confidence=0.85,
            reason="matched_revision_phrase",
            should_start_new_task=False,
            source="rule",
            requires_replan=False,
        )
    if context["has_pending_task"] and not context["has_active_generation"]:
        return TaskRoutingDecision(
            action="answer_pending_clarification",
            confidence=0.75,
            reason="pending_task_without_active_generation",
            should_start_new_task=False,
            source="heuristic",
            requires_replan=True,
        )
    if not context["has_active_generation"] and not context["has_pending_task"]:
        return TaskRoutingDecision(
            action="new_task",
            confidence=1.0,
            reason="no_active_or_pending_task",
            should_start_new_task=True,
            source="heuristic",
            requires_replan=True,
        )
    return None


def _active_generation_summary(active_generation: dict | None) -> str:
    if not active_generation:
        return "无"
    candidates = active_generation.get("candidates")
    if isinstance(candidates, list) and candidates:
        lines = [
            f"候选数：{len(candidates)}",
            f"当前选中：{active_generation.get('selected_index') or '未选择'}",
            f"修订次数：{active_generation.get('revision_count', 0)}",
        ]
        for candidate in candidates[:5]:
            score = candidate.get("score")
            lines.append(
                " | ".join(
                    [
                        f"{candidate.get('index', '?')}. {candidate.get('topic_title') or '未知选题'}",
                        f"标题：{candidate.get('content_title') or '未知'}",
                        f"评分：{score if score is not None else '未知'}",
                    ]
                )
            )
        return "\n".join(lines)

    topic = active_generation.get("topic_title", "")
    title = active_generation.get("content_title", "")
    score = active_generation.get("score", "")
    revision_count = active_generation.get("revision_count", 0)
    return (
        f"主题：{topic or '未知'}\n"
        f"当前标题：{title or '未知'}\n"
        f"当前评分：{score if score != '' else '未知'}\n"
        f"修订次数：{revision_count}"
    )


def _candidate_index_from_feedback(user_message: str) -> int | None:
    text = user_message.strip()
    for match in re.finditer(
        r"(?:第\s*([1-9]\d*)\s*(?:篇|个|条|版|选题|候选)?|([1-9]\d*)\s*(?:篇|条|版|选题|候选))",
        text,
    ):
        try:
            return int(match.group(1) or match.group(2))
        except ValueError:
            continue
    chinese_numbers = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5}
    for char, value in chinese_numbers.items():
        if f"第{char}" in text:
            return value
    return None


def _looks_like_fresh_generation_request(text: str) -> bool:
    if not text:
        return False
    explicit_candidate = re.search(r"第\s*[1-9一二两三四五]\s*(篇|个|条|版|选题|候选)?", text)
    if explicit_candidate:
        return False
    if any(word in text for word in ["这篇", "这版", "当前", "上一版", "候选"]):
        return False
    fresh_markers = [
        "写一篇",
        "写1篇",
        "帮我写",
        "生成一篇",
        "生成1篇",
        "来一篇",
        "做一期",
        "做一篇",
        "出一篇",
        "准备一篇",
        "我要写",
        "我想写",
    ]
    return any(marker in text for marker in fresh_markers)


def _looks_like_fresh_task_request(text: str) -> bool:
    if not text:
        return False
    route = _fallback_delivery_route(text)
    if route.get("final_deliverable") == "source_notes" and float(route.get("confidence") or 0) >= 0.8:
        return True
    return _looks_like_fresh_generation_request(text)


def _active_generation_candidates(active_generation: dict) -> list[dict]:
    candidates = active_generation.get("candidates")
    if isinstance(candidates, list) and candidates:
        return [item for item in candidates if isinstance(item, dict)]
    if "content" not in active_generation:
        return []
    return [
        {
            "index": 1,
            "topic_title": active_generation.get("topic_title"),
            "content_title": active_generation.get("content_title"),
            "content": active_generation.get("content"),
            "critique": active_generation.get("critique"),
            "score": active_generation.get("score"),
            "revision_count": active_generation.get("revision_count", 0),
        }
    ]


def _select_active_candidate(active_generation: dict, user_message: str) -> tuple[dict | None, int | None, bool]:
    candidates = _active_generation_candidates(active_generation)
    if not candidates:
        return None, None, False

    requested_index = _candidate_index_from_feedback(user_message)
    if requested_index is not None:
        for candidate in candidates:
            if int(candidate.get("index", 0)) == requested_index:
                return candidate, requested_index, False
        return None, requested_index, False

    selected_index = active_generation.get("selected_index")
    if selected_index:
        for candidate in candidates:
            if int(candidate.get("index", 0)) == int(selected_index):
                return candidate, int(selected_index), False

    if len(candidates) == 1:
        return candidates[0], int(candidates[0].get("index", 1)), False

    return None, None, True


async def _resolve_task_routing(
    container: AppContainer,
    state: PipelineState,
    active_generation: dict | None,
    pending_task: dict | None = None,
    active_task: dict | None = None,
) -> TaskRoutingDecision:
    span = begin_span(
        "task_routing",
        "resolve_task_routing",
        input_summary={
            "user_message_chars": len(state.user_message),
            "has_active_generation": bool(active_generation),
            "has_pending_task": bool(pending_task),
            "has_active_task": bool(active_task),
        },
    )
    context = _routing_context(active_generation, pending_task, active_task)
    has_active = context["has_active_generation"]
    rule_decision = _route_by_rules(state.user_message, context)
    if rule_decision is not None:
        span.end(
            output_summary={
                "action": rule_decision.action,
                "confidence": rule_decision.confidence,
                "source": rule_decision.source,
                "reason": rule_decision.reason,
                "selected_index": rule_decision.selected_index,
            }
        )
        return rule_decision

    if not container.llm_service.enabled or not state.user_message.strip():
        decision = TaskRoutingDecision(
            action="new_task" if not has_active else "ask_clarification",
            confidence=0.5,
            reason="llm_unavailable_or_empty_message",
            clarification_question="你是想修改上一版，还是开始一个新任务？" if has_active else "",
            should_start_new_task=not has_active,
            source="fallback",
            requires_replan=not has_active,
        )
        if context["has_pending_task"] and not has_active:
            decision.action = "answer_pending_clarification"
            decision.confidence = 0.6
            decision.should_start_new_task = False
            decision.requires_replan = True
        span.end(
            output_summary={
                "action": decision.action,
                "confidence": decision.confidence,
                "source": decision.source,
                "reason": decision.reason,
            }
        )
        return decision

    user = TASK_ROUTING_PROMPT.render_user(
        has_active_generation="是" if has_active else "否",
        active_generation_summary=_active_generation_summary(active_generation),
        last_system_action=context["last_system_action"] or "无",
        has_pending_task="是" if context["has_pending_task"] else "否",
        pending_question=context["pending_question"] or "无",
        publish_requested="是" if context["publish_requested"] else "否",
        candidate_count=context["candidate_count"],
        user_message=state.user_message,
    )
    try:
        result = await container.llm_service.chat_json(system=TASK_ROUTING_PROMPT.system, user=user)
        parsed = container.llm_service.extract_json(result.content) or {}
    except Exception as exc:
        span.end(status="failed", error=str(exc))
        raise
    action = str(parsed.get("action") or "new_task").strip()
    if action not in ROUTING_ACTIONS:
        action = "new_task"
    if not has_active and action not in {"new_task", "ask_clarification"}:
        action = "answer_pending_clarification" if context["has_pending_task"] else "new_task"
    if action == "abandon_active_generation":
        action = "abandon_task"

    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0

    selected_index = parsed.get("selected_index")
    try:
        selected_index = int(selected_index) if selected_index is not None else None
    except (TypeError, ValueError):
        selected_index = None

    decision = TaskRoutingDecision(
        action=action,
        confidence=max(0.0, min(confidence, 1.0)),
        reason=str(parsed.get("reason") or ""),
        clarification_question=str(parsed.get("clarification_question") or ""),
        should_start_new_task=action == "new_task",
        source="llm_router",
        selected_index=selected_index,
        requires_replan=action in {"new_task", "answer_pending_clarification"},
    )
    if not _should_execute_route(decision.action, decision.confidence):
        fallback_decision = TaskRoutingDecision(
            action="ask_clarification",
            confidence=1.0,
            reason=f"low_confidence_{decision.action}_{decision.confidence}",
            clarification_question=decision.clarification_question
            or _route_clarification_question(decision.action, context),
            should_start_new_task=False,
            source="confidence_gate",
            requires_replan=False,
        )
        span.end(
            output_summary={
                "action": fallback_decision.action,
                "confidence": fallback_decision.confidence,
                "source": fallback_decision.source,
                "reason": fallback_decision.reason,
            }
        )
        return fallback_decision
    span.end(
        output_summary={
            "action": decision.action,
            "confidence": decision.confidence,
            "source": decision.source,
            "reason": decision.reason,
            "selected_index": decision.selected_index,
        }
    )
    return decision


def _build_llm_context(state: PipelineState) -> str:
    note_count = len(state.input_notes)
    parts = [
        "[本轮用户问题]",
        state.user_message or "（未提供原始用户问题）",
    ]
    if state.search_query:
        parts.extend(["", "[搜索意图]", f"搜索Query：{state.search_query}"])
    if state.search_keywords:
        parts.append(f"搜索关键词：{', '.join(state.search_keywords)}")
    parts.extend([
        "",
        "[本轮任务约束]",
        f"受众：{state.audience}",
        f"语气：{state.tone}",
        f"选题数：{state.topic_count}",
        f"每个选题内容数：{state.content_count_per_topic}",
        f"输入样本数：{note_count}",
    ])
    if state.memory_context:
        parts.extend(["", "[历史上下文摘要]", state.memory_context])
    if state.metadata.get("active_generation"):
        parts.extend([
            "",
            "[当前活跃生成任务]",
            _active_generation_summary(state.metadata.get("active_generation")),
            f"任务路由：{state.task_routing.action}",
        ])
    return "\n".join(parts)


def _build_stage_plan(state: PipelineState) -> list[PipelineStage]:
    plan = state.plan
    stages: list[PipelineStage] = []
    if plan.needs_crawl:
        stages.append(PipelineStage.CRAWLING)
    if plan.needs_analysis:
        stages.append(PipelineStage.ANALYZING)
    if plan.needs_topic_generation:
        stages.append(PipelineStage.TOPIC_GENERATING)
    if plan.needs_content_generation:
        stages.append(PipelineStage.CONTENT_GENERATING)
    if plan.needs_review:
        stages.append(PipelineStage.REVIEWING)
    if plan.needs_publish:
        stages.append(PipelineStage.PUBLISHING)
    return stages or [
        PipelineStage.CRAWLING,
        PipelineStage.ANALYZING,
        PipelineStage.TOPIC_GENERATING,
        PipelineStage.CONTENT_GENERATING,
        PipelineStage.REVIEWING,
    ]


def _apply_delivery_route_to_plan(state: PipelineState) -> None:
    route = state.metadata.get("delivery_route")
    if not isinstance(route, dict):
        return

    deliverable = route.get("final_deliverable")
    if deliverable == "source_notes":
        state.plan.intent = "crawl_only"
        state.plan.needs_crawl = True
        state.plan.needs_analysis = True
        state.plan.needs_topic_generation = False
        state.plan.needs_content_generation = False
        state.plan.needs_review = False
        state.plan.needs_publish = False
    elif deliverable == "analysis_report":
        state.plan.intent = "crawl_only"
        state.plan.needs_crawl = True
        state.plan.needs_analysis = True
        state.plan.needs_topic_generation = False
        state.plan.needs_content_generation = False
        state.plan.needs_review = False
        state.plan.needs_publish = False
    elif deliverable == "topic_list":
        state.plan.intent = "topic_only"
        state.plan.needs_crawl = True
        state.plan.needs_analysis = True
        state.plan.needs_topic_generation = True
        state.plan.needs_content_generation = False
        state.plan.needs_review = False
        state.plan.needs_publish = False
    elif deliverable == "publish_ready_content":
        state.plan.intent = "publish_post"
        use_crawl = state.mode == "deep" or _has_explicit_crawl_request(state.user_message)
        state.plan.needs_crawl = use_crawl
        state.plan.needs_analysis = use_crawl
        state.plan.needs_topic_generation = True
        state.plan.needs_content_generation = True
        state.plan.needs_review = state.mode == "deep"
        state.plan.needs_publish = True
    elif deliverable == "content_draft":
        if state.plan.intent in {"crawl_only", "topic_only", "clarify_request"}:
            state.plan.intent = "full_post"
        use_crawl = state.mode == "deep" or _has_explicit_crawl_request(state.user_message)
        state.plan.needs_crawl = use_crawl
        state.plan.needs_analysis = use_crawl
        state.plan.needs_topic_generation = True
        state.plan.needs_content_generation = True
        state.plan.needs_review = state.mode == "deep"
        if not use_crawl:
            state.metadata["skip_reason"] = "fast_mode_no_explicit_crawl_request"

    topic = str(route.get("topic") or "").strip()
    if topic:
        search_topic = _clean_source_note_search_topic(topic) if deliverable == "source_notes" else topic
        state.plan.topic_seed = search_topic or state.plan.topic_seed or topic
        if deliverable == "source_notes" and search_topic:
            state.plan.search_query = search_topic
            state.plan.search_keywords = _extract_keywords_rule_based(search_topic)
            state.search_query = search_topic
            state.search_keywords = state.plan.search_keywords
        else:
            state.plan.search_query = state.plan.search_query or topic
    if not state.plan.search_query:
        state.plan.search_query = state.search_query or state.user_message
    if not state.plan.search_keywords:
        state.plan.search_keywords = _extract_keywords_rule_based(state.plan.search_query)

    count = int(route.get("count") or 0)
    if count and deliverable == "source_notes":
        state.final_note_limit = max(state.final_note_limit, count)
        state.raw_crawl_limit = max(state.raw_crawl_limit, count * 2)
        state.metadata["requested_source_note_count"] = count

    ordered = [
        (PipelineStage.CRAWLING, state.plan.needs_crawl),
        (PipelineStage.ANALYZING, state.plan.needs_analysis),
        (PipelineStage.TOPIC_GENERATING, state.plan.needs_topic_generation),
        (PipelineStage.CONTENT_GENERATING, state.plan.needs_content_generation),
        (PipelineStage.REVIEWING, state.plan.needs_review),
        (PipelineStage.PUBLISHING, state.plan.needs_publish),
    ]
    ready_assigned = False
    items: list[PlannedStageItem] = []
    for stage, enabled in ordered:
        status = PlannedStageStatus.SKIPPED
        if enabled:
            status = PlannedStageStatus.READY if not ready_assigned else PlannedStageStatus.PENDING
            ready_assigned = True
        items.append(PlannedStageItem(stage=stage, status=status))
    state.plan.planned_stages = items
    state.metadata["planned_stages"] = [
        item.stage.value if hasattr(item.stage, "value") else str(item.stage)
        for item in items
        if item.status != PlannedStageStatus.SKIPPED
    ]
    state.metadata["skipped_stages"] = [
        item.stage.value if hasattr(item.stage, "value") else str(item.stage)
        for item in items
        if item.status == PlannedStageStatus.SKIPPED
    ]
    state.metadata["pipeline_plan"] = state.plan.model_dump()


def _defer_publish_until_user_confirmation(state: PipelineState) -> None:
    if not state.plan.needs_publish:
        return
    state.metadata["publish_requested"] = True
    state.metadata["publish_confirmation_required"] = True
    state.plan.needs_publish = False
    for item in state.plan.planned_stages:
        if item.stage == PipelineStage.PUBLISHING:
            item.status = PlannedStageStatus.SKIPPED
    state.metadata["pipeline_plan"] = state.plan.model_dump()


def _build_publish_confirmation_question(candidate_count: int) -> str:
    if candidate_count > 1:
        return (
            f"文案已经生成并审核完成，共 {candidate_count} 篇候选。"
            "发布前请先确认要发哪一篇，例如回复“发布第1篇”；如果不满意，也可以直接说要改哪里。"
        )
    return "文案已经生成并审核完成。发布前请先确认这版是否可以发布；如果不满意，也可以直接说要改哪里。"


def _build_revision_analysis_brief(analysis: AnalysisResult | None) -> str:
    if analysis is None:
        return "暂无热帖分析结果，请只根据上一版文案和用户反馈修订。"
    strategy = analysis.writing_strategy
    structural = analysis.structural_patterns
    insights = analysis.content_insights
    engagement = analysis.engagement_signals
    return "\n".join(
        [
            f"分析摘要：{analysis.summary}",
            f"高频关键词：{', '.join(analysis.top_keywords[:8]) or '暂无'}",
            f"高频标签：{', '.join(analysis.top_tags[:8]) or '暂无'}",
            f"标题钩子词：{', '.join(structural.hook_words[:6]) or '暂无'}",
            f"内容价值类型：{engagement.content_value_type or '暂无'}",
            f"核心痛点：{insights.core_user_pain or '暂无'}",
            f"推荐标题公式：{strategy.recommended_title_formula or '暂无'}",
            f"开头策略：{strategy.opening_strategy or '暂无'}",
            f"正文结构：{'; '.join(strategy.body_structure) or '暂无'}",
            f"可信度打法：{strategy.credibility_tactics or '暂无'}",
            f"结尾 CTA：{strategy.closing_cta or '暂无'}",
            f"必含元素：{', '.join(strategy.must_include_elements[:8]) or '暂无'}",
            f"避免模式：{', '.join(strategy.avoid_patterns[:8]) or '暂无'}",
        ]
    )


def _fallback_revise_content(previous: ContentItem, feedback: str) -> ContentItem:
    title = previous.title
    if "标题" in feedback and not any(ch.isdigit() for ch in title):
        title = f"3个重点｜{title}"
    body = previous.body
    if "真实" in feedback or "细节" in feedback:
        body += "\n\n我会补充一个更具体的使用场景：先看自己的预算和实际需求，再决定要不要照搬推荐。"
    elif "口语" in feedback or "语气" in feedback:
        body += "\n\n这版我会写得更像真实分享，少一点模板感，多一点自己的判断。"
    else:
        body += f"\n\n根据你的反馈，我把重点调整为：{feedback}"
    cta = previous.cta if previous.cta.strip().endswith(("?", "？")) else f"{previous.cta.rstrip('。')}？"
    hashtags = previous.hashtags if len(previous.hashtags) >= 3 else [*previous.hashtags, "真实分享", "小红书文案"][:6]
    return ContentItem(
        title=title,
        body=body,
        hashtags=hashtags,
        cta=cta,
        image_suggestion=previous.image_suggestion,
        content_type=previous.content_type,
    )


async def _run_revision_pipeline(
    container: AppContainer,
    state: PipelineState,
    active_generation: dict,
) -> AgentRunResponse:
    if not active_generation:
        state.stage = PipelineStage.WAITING_FOR_INPUT
        state.plan.needs_clarification = True
        state.plan.clarification_question = "没有找到可修改的上一版内容。你可以重新发起一个生成任务。"
        state.plan.clarification_fields = ["active_generation"]
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )

    selected_candidate, selected_index, needs_candidate_choice = _select_active_candidate(
        active_generation,
        state.user_message,
    )
    if needs_candidate_choice:
        state.stage = PipelineStage.WAITING_FOR_INPUT
        state.plan.needs_clarification = True
        state.plan.clarification_question = "你想修改哪一篇？请回复第1篇、第2篇或第3篇，并带上修改意见。"
        state.plan.clarification_fields = ["selected_candidate"]
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )
    if selected_candidate is None:
        state.stage = PipelineStage.WAITING_FOR_INPUT
        state.plan.needs_clarification = True
        state.plan.clarification_question = f"没有找到第 {selected_index} 篇候选，请重新选择要修改的候选编号。"
        state.plan.clarification_fields = ["selected_candidate"]
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )

    previous = ContentItem(**selected_candidate["content"])
    analysis_payload = active_generation.get("analysis")
    analysis = AnalysisResult(**analysis_payload) if analysis_payload else None
    critique_payload = selected_candidate.get("critique") or {}
    candidate_score = selected_candidate.get("score")
    if candidate_score is not None:
        active_generation["score"] = candidate_score
    previous_score = critique_payload.get("total_score", active_generation.get("score", "未知"))
    previous_issues = "; ".join(critique_payload.get("issues", []) or []) or "无"
    previous_suggestions = "; ".join(critique_payload.get("suggestions", []) or []) or "无"

    shared_context = (
        f"{_build_llm_context(state)}"
        if state.llm_context
        else _build_llm_context(state)
    )

    revised = None
    if container.llm_service.enabled:
        user = REVISION_PROMPT.render_user(
            shared_context=shared_context,
            user_feedback=state.user_message,
            previous_title=previous.title,
            previous_body=previous.body,
            previous_hashtags=", ".join(previous.hashtags),
            previous_cta=previous.cta,
            previous_content_type=previous.content_type,
            previous_score=previous_score,
            previous_issues=previous_issues,
            previous_suggestions=previous_suggestions,
            analysis_brief=_build_revision_analysis_brief(analysis),
        )
        result = await container.llm_service.chat_json(system=REVISION_PROMPT.system, user=user)
        parsed = container.llm_service.extract_json(result.content) or {}
        item = parsed.get("content")
        if isinstance(item, dict):
            try:
                revised = ContentItem(**item)
            except Exception:  # noqa: BLE001
                revised = None

    if revised is None:
        revised = _fallback_revise_content(previous, state.user_message)

    critique = await container.evaluation_service.review(
        content=revised,
        analysis=analysis,
        state=state,
    )
    try:
        container.memory_manager.record_user_revision_feedback(
            user_id=state.user_id,
            feedback=state.user_message,
        )
        container.memory_manager.record_pattern_feedback(
            user_id=state.user_id,
            critiques=[critique],
        )
    except Exception:  # noqa: BLE001
        pass

    topic = TopicItem(
        title=str(active_generation.get("topic_title") or "上一版选题"),
        reason="基于用户对上一版文案的反馈进行修订。",
    )
    block = GeneratedTopicWithContents(topic=topic, contents=[revised], critique=critique)
    state.results = [block]
    state.analysis = analysis
    state.stage = PipelineStage.COMPLETED
    draft_package = container.draft_service.save_pipeline_draft(state)
    if draft_package:
        state.metadata["draft_package"] = draft_package.model_dump()
    state.ai_message = f"已根据你的反馈完成第 {int(active_generation.get('revision_count', 0)) + 2} 版修订。"

    revision_count = int(active_generation.get("revision_count", 0)) + 1
    updated_candidates = _active_generation_candidates(active_generation)
    for candidate in updated_candidates:
        if int(candidate.get("index", 0)) == int(selected_index or 1):
            candidate.update(
                {
                    "content_title": revised.title,
                    "content": revised.model_dump(),
                    "critique": critique.model_dump(),
                    "score": critique.total_score,
                    "revision_count": int(candidate.get("revision_count", 0)) + 1,
                    "updated_at": time.time(),
                }
            )
    container.session_service.save_active_generation(
        state.user_id,
        state.session_id,
        {
            **active_generation,
            "session_id": state.session_id,
            "task_id": state.task_id,
            "status": "awaiting_feedback",
            "selected_index": selected_index,
            "candidates": updated_candidates,
            "content_title": revised.title,
            "content": revised.model_dump(),
            "critique": critique.model_dump(),
            "score": critique.total_score,
            "draft_package": draft_package.model_dump() if draft_package else active_generation.get("draft_package"),
            "revision_count": revision_count,
            "updated_at": time.time(),
        },
    )
    container.session_service.save(state)

    return AgentRunResponse(
        run_id=state.run_id,
        session_id=state.session_id,
        task_id=state.task_id,
        stage=state.stage,
        failed=False,
        error_message=None,
        search_query=state.search_query,
        search_keywords=state.search_keywords,
        input_note_count=len(state.input_notes),
        analysis_summary=analysis.summary if analysis else "",
        top_keywords=analysis.top_keywords if analysis else [],
        top_tags=analysis.top_tags if analysis else [],
        title_patterns=analysis.title_patterns if analysis else [],
        insight_points=analysis.insight_points if analysis else [],
        draft_package=draft_package,
        source_notes=state.input_notes,
        results=state.results,
    )


async def _run_publish_confirmation(
    container: AppContainer,
    state: PipelineState,
    active_generation: dict,
) -> AgentRunResponse:
    selected_candidate, selected_index, needs_candidate_choice = _select_active_candidate(
        active_generation,
        state.user_message,
    )
    if needs_candidate_choice:
        state.stage = PipelineStage.WAITING_FOR_INPUT
        state.plan.needs_clarification = True
        state.plan.clarification_question = "发布前请先确认要发哪一篇，例如回复“发布第1篇”。"
        state.plan.clarification_fields = ["selected_candidate"]
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )
    if selected_candidate is None:
        state.stage = PipelineStage.WAITING_FOR_INPUT
        state.plan.needs_clarification = True
        state.plan.clarification_question = f"没有找到第 {selected_index} 篇候选，请重新选择要发布的候选编号。"
        state.plan.clarification_fields = ["selected_candidate"]
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )

    content = ContentItem(**selected_candidate["content"])
    topic = TopicItem(
        title=str(selected_candidate.get("topic_title") or active_generation.get("topic_title") or "待发布文案"),
        reason=str(selected_candidate.get("topic_reason") or "用户确认后发布。"),
    )
    critique_payload = selected_candidate.get("critique")
    block = GeneratedTopicWithContents(topic=topic, contents=[content])
    if critique_payload:
        from models.evaluation import ReviewCritique

        block.critique = ReviewCritique(**critique_payload)

    publish_state = PipelineState(
        run_id=str(uuid.uuid4()),
        session_id=state.session_id,
        task_id=state.task_id,
        mode=state.mode,
        stage=PipelineStage.PUBLISHING,
        user_id=state.user_id,
        user_message=state.user_message,
        search_query=state.search_query,
        search_keywords=state.search_keywords,
        audience=state.audience,
        tone=state.tone,
        results=[block],
        metadata={
            "parent_run_id": active_generation.get("parent_run_id"),
            "selected_index": selected_index,
            "publish_confirmed": True,
        },
    )
    publish_state = await container.publisher_agent.run(publish_state)
    publish_state.stage = PipelineStage.FAILED if publish_state.failed else PipelineStage.COMPLETED
    if not publish_state.failed:
        container.session_service.clear_active_generation(state.user_id, state.session_id)
        if hasattr(container.session_service, "clear_active_task"):
            container.session_service.clear_active_task(state.user_id, state.session_id)
    container.session_service.save(publish_state)

    return AgentRunResponse(
        run_id=publish_state.run_id,
        session_id=publish_state.session_id,
        task_id=publish_state.task_id,
        stage=publish_state.stage,
        failed=publish_state.failed,
        error_message=publish_state.error_message,
        search_query=publish_state.search_query,
        search_keywords=publish_state.search_keywords,
        publish_record=publish_state.metadata.get("publish_record"),
        results=publish_state.results,
    )


async def run_agent_pipeline(container: AppContainer, request: AgentRunRequest) -> AgentRunResponse:
    session_id = request.session_id.strip() or "default"
    run_id = str(uuid.uuid4())
    set_trace_id(run_id)
    pending_task = container.session_service.get_pending_task(request.user_id, session_id)
    resumes_pending_task = (
        pending_task is not None
        and (not request.task_id or request.task_id == pending_task.get("task_id"))
        and not _looks_like_new_task_while_pending(request.user_message)
    )
    if pending_task and not resumes_pending_task:
        container.session_service.clear_pending_task(request.user_id, session_id)

    user_message = request.user_message.strip() or _build_default_user_message(request)
    if resumes_pending_task:
        user_message = _merge_pending_user_message(pending_task, user_message)

    active_task = container.session_service.get_active_task(request.user_id, session_id)
    active_generation = container.session_service.get_active_generation(request.user_id, session_id)
    if active_generation and not request.task_id and _looks_like_fresh_task_request(request.user_message):
        container.session_service.clear_active_generation(request.user_id, session_id)
        container.session_service.clear_active_task(request.user_id, session_id)
        active_generation = None
        active_task = None
        state_cleared_for_new_task = True
    else:
        state_cleared_for_new_task = False
    task_id = (
        request.task_id
        or (pending_task.get("task_id") if resumes_pending_task else None)
        or (active_generation.get("task_id") if active_generation else None)
        or str(uuid.uuid4())
    )
    previous_state_payload = pending_task.get("pipeline_state") if resumes_pending_task else None
    previous_state = None
    if isinstance(previous_state_payload, dict):
        try:
            previous_state = PipelineState(**previous_state_payload)
        except Exception:  # noqa: BLE001
            previous_state = None
    state = PipelineState(
        run_id=run_id,
        session_id=session_id,
        task_id=task_id,
        mode=request.mode,
        stage=PipelineStage.IDLE,
        user_id=request.user_id,          # 透传 user_id，记忆系统用此区分用户
        user_message=user_message,
        search_query=request.search_query or (previous_state.search_query if previous_state else ""),
        search_keywords=_normalize_keywords(request.search_keywords)
        or (previous_state.search_keywords if previous_state else []),
        raw_crawl_limit=request.raw_crawl_limit,
        final_note_limit=request.final_note_limit,
        min_final_note_count=request.min_final_note_count,
        audience=request.audience or (previous_state.audience if previous_state else ""),
        tone=request.tone or (previous_state.tone if previous_state else ""),
        topic_count=request.topic_count,
        content_count_per_topic=request.content_count_per_topic,
        review_threshold=request.review_threshold,
        max_reflections=request.max_reflections,
        candidate_notes=request.candidate_notes or request.items or (previous_state.candidate_notes if previous_state else []),
    )
    _apply_mode_defaults(state)
    if previous_state:
        state.metadata["resumed_from_pending_task"] = pending_task.get("run_id")
    if resumes_pending_task and pending_task and pending_task.get("type") == "material_clarification":
        state.metadata["material_clarification_handled"] = True
        state.metadata["material_reply"] = request.user_message.strip()
        if _is_skip_material_reply(request.user_message):
            state.metadata["material_clarification_skipped"] = True
    if state_cleared_for_new_task:
        state.metadata["cleared_stale_active_generation"] = True
    state.metadata["has_explicit_user_message"] = bool(request.user_message.strip())
    requested_note_count = _requested_source_note_count(user_message)
    if requested_note_count:
        state.final_note_limit = max(state.final_note_limit, requested_note_count)
        state.raw_crawl_limit = max(state.raw_crawl_limit, requested_note_count * 2)
        state.metadata["requested_source_note_count"] = requested_note_count
    if active_generation:
        state.metadata["active_generation"] = active_generation
    if active_task:
        state.metadata["active_task"] = active_task
    state.task_routing = await _resolve_task_routing(container, state, active_generation, pending_task, active_task)
    container.session_service.save(state)

    if state.task_routing.action in {"select_candidate", "confirm_active_generation"}:
        if (
            state.task_routing.action == "select_candidate"
            and active_generation
            and state.task_routing.selected_index is not None
        ):
            active_generation["selected_index"] = state.task_routing.selected_index
            container.session_service.save_active_generation(
                state.user_id,
                state.session_id,
                active_generation,
            )
            state.stage = PipelineStage.WAITING_FOR_INPUT
            state.plan.needs_clarification = True
            state.plan.clarification_question = "已选中这篇候选。你想继续修改、确认使用，还是发布？"
            state.plan.clarification_fields = ["next_action"]
            container.session_service.save(state)
            return AgentRunResponse(
                run_id=state.run_id,
                session_id=state.session_id,
                task_id=state.task_id,
                stage=state.stage,
                failed=False,
                needs_clarification=True,
                clarification_question=state.plan.clarification_question,
                clarification_fields=state.plan.clarification_fields,
                search_query=state.search_query,
                search_keywords=state.search_keywords,
                results=[],
            )
        if active_generation and active_generation.get("publish_requested"):
            return await _run_publish_confirmation(container, state, active_generation)
        container.session_service.clear_active_generation(state.user_id, state.session_id)
        container.session_service.clear_active_task(state.user_id, state.session_id)
        state.stage = PipelineStage.COMPLETED
        state.ai_message = "已确认最终版。"
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            error_message=None,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )

    if state.task_routing.action in {"abandon_task", "abandon_active_generation"}:
        container.session_service.clear_active_generation(state.user_id, state.session_id)
        container.session_service.clear_active_task(state.user_id, state.session_id)
        container.session_service.clear_pending_task(state.user_id, state.session_id)
        state.stage = PipelineStage.COMPLETED
        state.ai_message = "已放弃当前生成任务。"
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            error_message=None,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )

    if state.task_routing.action == "confirm_publish":
        if not active_generation:
            state.stage = PipelineStage.WAITING_FOR_INPUT
            state.plan.needs_clarification = True
            state.plan.clarification_question = "没有找到待发布的草稿。你可以先发起一个生成任务。"
            state.plan.clarification_fields = ["active_generation"]
            container.session_service.save(state)
            return AgentRunResponse(
                run_id=state.run_id,
                session_id=state.session_id,
                task_id=state.task_id,
                stage=state.stage,
                failed=False,
                needs_clarification=True,
                clarification_question=state.plan.clarification_question,
                clarification_fields=state.plan.clarification_fields,
                search_query=state.search_query,
                search_keywords=state.search_keywords,
                results=[],
            )
        return await _run_publish_confirmation(container, state, active_generation)

    if state.task_routing.action == "revise_active_generation":
        state.llm_context = _build_llm_context(state)
        container.session_service.save(state)
        return await _run_revision_pipeline(container, state, active_generation)

    if state.task_routing.action == "ask_clarification":
        state.stage = PipelineStage.WAITING_FOR_INPUT
        state.plan.needs_clarification = True
        state.plan.clarification_question = (
            state.task_routing.clarification_question
            or "你是想修改上一版内容，还是开始一个新任务？"
        )
        state.plan.clarification_fields = ["task_routing"]
        state.plan.clarification_tips = "请说明是继续修改上一版，还是开启新任务。"
        container.session_service.save_pending_task(
            state.user_id,
            state.session_id,
            _pending_task_payload(state, resume_mode="route_decision"),
        )
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            error_message=None,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            clarification_tips=state.plan.clarification_tips,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            results=[],
        )

    if state.task_routing.action == "new_task" and (active_generation or active_task):
        container.session_service.clear_active_generation(state.user_id, state.session_id)
        container.session_service.clear_active_task(state.user_id, state.session_id)
        state.metadata.pop("active_generation", None)
        state.metadata.pop("active_task", None)
        state.task_id = request.task_id or str(uuid.uuid4())

    if state.task_routing.action == "new_task":
        state.metadata["delivery_route"] = await _resolve_delivery_route(container, state)
    route = state.metadata.get("delivery_route")
    if (
        isinstance(route, dict)
        and route.get("final_deliverable") == "source_notes"
        and not state.candidate_notes
        and CrawlerAgent._is_login_state_stale()
    ):
        _mark_xhs_login_required(state)
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            error_message=None,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            clarification_tips=state.plan.clarification_tips,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            input_note_count=0,
            results=[],
        )

    # ── pipeline 开始前：读取历史记忆注入 State ───────────────────────────────
    # 优先使用完整用户原话做检索，避免只靠受众+语气导致召回失真。
    memory_query = state.user_message
    try:
        state.memory_context = await container.memory_manager.build_memory_context(
            user_id=request.user_id,
            session_id=state.session_id,
            query=memory_query,
        )
    except Exception:  # noqa: BLE001
        # 记忆读取失败不阻断主流程
        state.memory_context = ""
    state.llm_context = _build_llm_context(state)
    container.session_service.save(state)

    route = state.metadata.get("delivery_route")
    if (
        isinstance(route, dict)
        and route.get("final_deliverable") == "source_notes"
        and float(route.get("confidence") or 0) >= 0.8
    ):
        state.metadata["planner_skipped_reason"] = "rule_based_source_notes"
        _apply_delivery_route_to_plan(state)
    else:
        state = await container.planner_agent.run(state)
        _apply_delivery_route_to_plan(state)
    plan_span = begin_span(
        "workflow",
        "materialize_stage_plan",
        input_summary={
            "mode": state.mode,
            "route": state.metadata.get("delivery_route"),
        },
    )
    plan_span.end(
        output_summary={
            "mode": state.mode,
            "planned_stages": state.metadata.get("planned_stages", []),
            "skipped_stages": state.metadata.get("skipped_stages", []),
            "skip_reason": state.metadata.get("skip_reason", ""),
        }
    )
    state.llm_context = _build_llm_context(state)
    container.session_service.save(state)

    if state.plan.needs_clarification:
        state.stage = PipelineStage.WAITING_FOR_INPUT
        container.session_service.save_pending_task(
            state.user_id,
            state.session_id,
            _pending_task_payload(state, resume_mode="replan"),
        )
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            error_message=None,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            clarification_tips=state.plan.clarification_tips,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            input_note_count=0,
            analysis_summary="",
            top_keywords=[],
            top_tags=[],
            title_patterns=[],
            insight_points=[],
            results=[],
        )

    material_topic = state.plan.topic_seed or state.search_query or state.user_message
    content_mode = ContentAgent._infer_content_mode(state, material_topic, state.plan.search_query)
    state.metadata["content_mode"] = content_mode
    state.metadata["has_user_material"] = ContentAgent._has_user_material(state.user_message)
    if _should_ask_for_material(state, content_mode):
        state.stage = PipelineStage.WAITING_FOR_INPUT
        state.plan.needs_clarification = True
        state.plan.clarification_question = _material_question_for(content_mode, material_topic)
        state.plan.clarification_fields = ["material_context"]
        state.plan.clarification_tips = "有真实素材会更像本人表达；没有也可以跳过，我会改成非亲历写法。"
        container.session_service.save_pending_task(
            state.user_id,
            state.session_id,
            _material_pending_task_payload(state, content_mode),
        )
        container.session_service.save(state)
        return AgentRunResponse(
            run_id=state.run_id,
            session_id=state.session_id,
            task_id=state.task_id,
            stage=state.stage,
            failed=False,
            error_message=None,
            needs_clarification=True,
            clarification_question=state.plan.clarification_question,
            clarification_fields=state.plan.clarification_fields,
            clarification_tips=state.plan.clarification_tips,
            search_query=state.search_query,
            search_keywords=state.search_keywords,
            input_note_count=0,
            analysis_summary="",
            top_keywords=[],
            top_tags=[],
            title_patterns=[],
            insight_points=[],
            results=[],
        )

    if resumes_pending_task:
        container.session_service.clear_pending_task(state.user_id, state.session_id)

    _defer_publish_until_user_confirmation(state)
    container.session_service.save(state)

    hooks = AgentRunHooks()
    engine = AgentLoopEngine(
        stage_agents={
            PipelineStage.CRAWLING: container.crawler_agent,
            PipelineStage.ANALYZING: container.analysis_agent,
            PipelineStage.TOPIC_GENERATING: container.topic_agent,
            PipelineStage.CONTENT_GENERATING: container.content_agent,
            PipelineStage.REVIEWING: container.reviewer_agent,   # 内部写回记忆
            PipelineStage.PUBLISHING: container.publisher_agent,
        },
        save_state=container.session_service.save,
        stage_plan=_build_stage_plan(state),
    )
    state = await engine.run(state, hooks=hooks)
    _maybe_convert_xhs_login_failure(state)
    _maybe_convert_source_note_crawl_failure(state)
    if state.metadata.get("crawl_failure_recovered") or state.metadata.get("xhs_login_failure_recovered"):
        container.session_service.save(state)

    publish_requested = bool(state.metadata.get("publish_requested"))
    draft_package = container.draft_service.save_pipeline_draft(state)
    if draft_package:
        state.metadata["draft_package"] = draft_package.model_dump()
        container.session_service.save(state)

    candidates: list[dict] = []
    if state.results:
        for block in state.results:
            for content in block.contents:
                candidates.append(
                    {
                        "index": len(candidates) + 1,
                        "topic_title": block.topic.title,
                        "topic_reason": block.topic.reason,
                        "content_title": content.title,
                        "content": content.model_dump(),
                        "critique": block.critique.model_dump() if block.critique else None,
                        "score": block.critique.total_score if block.critique else None,
                        "revision_count": 0,
                        "updated_at": time.time(),
                    }
                )
        if candidates:
            first = candidates[0]
            active_task = _active_task_payload(
                state,
                candidates=candidates,
                draft_package=draft_package,
                publish_requested=publish_requested,
            )
            container.session_service.save_active_task(state.user_id, state.session_id, active_task)
            container.session_service.save_active_generation(
                state.user_id,
                state.session_id,
                {
                    "parent_run_id": state.run_id,
                    "session_id": state.session_id,
                    "task_id": state.task_id,
                    "mode": state.mode,
                    "status": "awaiting_publish_confirmation" if publish_requested else "awaiting_selection",
                    "publish_requested": publish_requested,
                    "candidates": candidates,
                    "selected_index": None,
                    "topic_title": first.get("topic_title"),
                    "content_title": first.get("content_title"),
                    "content": first.get("content"),
                    "critique": first.get("critique"),
                    "analysis": state.analysis.model_dump() if state.analysis else None,
                    "score": first.get("score"),
                    "draft_package": draft_package.model_dump() if draft_package else None,
                    "revision_count": 0,
                    "updated_at": time.time(),
                },
            )
            if publish_requested and not state.failed:
                state.stage = PipelineStage.WAITING_FOR_INPUT
                state.plan.needs_clarification = True
                state.plan.clarification_question = _build_publish_confirmation_question(len(candidates))
                state.plan.clarification_fields = ["publish_confirmation"]
                state.plan.clarification_tips = "发布动作会等你确认后再执行。"
                state.ai_message = state.plan.clarification_question
                container.session_service.save(state)
    elif state.input_notes or state.analysis:
        container.session_service.save_active_task(
            state.user_id,
            state.session_id,
            _active_task_payload(state, draft_package=draft_package, publish_requested=False),
        )

    analysis = state.analysis
    return AgentRunResponse(
        run_id=state.run_id,
        session_id=state.session_id,
        task_id=state.task_id,
        stage=state.stage,
        failed=state.failed,
        error_message=state.error_message,
        needs_clarification=state.plan.needs_clarification,
        clarification_question=state.plan.clarification_question,
        clarification_fields=state.plan.clarification_fields,
        clarification_tips=state.plan.clarification_tips,
        search_query=state.search_query,
        search_keywords=state.search_keywords,
        input_note_count=len(state.input_notes),
        analysis_summary=analysis.summary if analysis else "",
        top_keywords=analysis.top_keywords if analysis else [],
        top_tags=analysis.top_tags if analysis else [],
        title_patterns=analysis.title_patterns if analysis else [],
        insight_points=analysis.insight_points if analysis else [],
        draft_package=draft_package,
        results=state.results,
        source_notes=state.input_notes,
    )


async def analyze_only(container: AppContainer, request: AnalyzeRequest) -> AnalyzeResponse:
    state = PipelineState(run_id=str(uuid.uuid4()), input_notes=request.items)
    state = await container.analysis_agent.run(state)
    if not state.analysis:
        raise ValueError(state.error_message or "Analysis failed.")
    result = state.analysis
    return AnalyzeResponse(
        total_count=len(request.items),
        top_keywords=result.top_keywords,
        top_tags=result.top_tags,
        title_patterns=result.title_patterns,
        insight_points=result.insight_points,
        summary=result.summary,
    )


async def generate_topics_only(container: AppContainer, request: TopicGenerateRequest) -> TopicGenerateResponse:
    from models.schemas import AnalysisResult

    state = PipelineState(
        run_id=str(uuid.uuid4()),
        topic_count=request.count,
        audience=request.audience,
    )
    state.analysis = AnalysisResult(
        summary=request.summary,
        top_keywords=request.top_keywords,
        top_tags=request.top_tags,
        title_patterns=request.title_patterns,
        insight_points=request.insight_points,
    )
    state = await container.topic_agent.run(state)
    return TopicGenerateResponse(topics=state.topics)


async def generate_content_only(
    container: AppContainer, request: ContentGenerateRequest
) -> ContentGenerateResponse:
    from models.schemas import GeneratedTopicWithContents, TopicItem

    state = PipelineState(
        run_id=str(uuid.uuid4()),
        audience=request.audience,
        tone=request.tone,
        content_count_per_topic=request.count,
        topics=[TopicItem(title=request.topic, reason=request.reason)],
    )
    state = await container.content_agent.run(state)
    result = state.results[0] if state.results else GeneratedTopicWithContents(topic=state.topics[0], contents=[])
    return ContentGenerateResponse(contents=result.contents)
