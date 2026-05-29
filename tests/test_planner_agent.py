import asyncio
import json

from agents.planner_agent import PlannerAgent
from models.schemas import PipelineState
from models.states import PipelineStage
from services.llm_service import LLMResult, LLMService


class StaticLLM:
    enabled = True

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def chat(self, system: str, user: str) -> LLMResult:
        return LLMResult(content=json.dumps(self.payload, ensure_ascii=False), input_tokens=10, output_tokens=10)

    @staticmethod
    def extract_json(text: str) -> dict | None:
        return LLMService.extract_json(text)


def _base_plan(**overrides):
    payload = {
        "intent": "full_post",
        "needs_clarification": False,
        "clarification_question": "",
        "clarification_fields": [],
        "clarification_tips": "",
        "topic_seed": "秋冬保湿",
        "planned_stages": [{"stage": "CRAWLING", "status": "ready"}],
        "needs_crawl": True,
        "needs_analysis": True,
        "needs_topic_generation": True,
        "needs_content_generation": True,
        "needs_review": True,
        "needs_publish": False,
        "search_query": "秋冬保湿",
        "search_keywords": ["秋冬保湿", "保湿护理"],
        "audience": "",
        "tone": "",
        "topic_count": 3,
        "content_count_per_topic": 1,
    }
    payload.update(overrides)
    return payload


def test_planner_upgrades_actionable_clarify_request_to_full_post():
    state = PipelineState(
        run_id="planner-clarify-upgrade",
        user_message="我想做一期关于秋冬保湿的内容",
    )
    state.metadata["has_explicit_user_message"] = True
    llm = StaticLLM(
        _base_plan(
            intent="clarify_request",
            needs_clarification=True,
            clarification_question="还需要补充内容目标。",
            clarification_fields=["goal"],
            clarification_tips="信息越完整，生成质量越高。",
        )
    )

    result = asyncio.run(PlannerAgent(llm).run(state))

    assert result.failed is False
    assert result.plan.intent == "full_post"
    assert result.plan.needs_clarification is False
    assert result.plan.needs_crawl is True
    assert result.plan.needs_analysis is True
    assert result.plan.needs_topic_generation is True
    assert result.plan.needs_content_generation is True
    assert result.plan.needs_review is True


def test_research_collection_request_is_crawl_only():
    assert (
        PlannerAgent._normalize_intent(
            "",
            "给我整理10个底层翻身的成功人士的帖子",
        )
        == "crawl_only"
    )


def test_counted_hot_post_request_is_crawl_only():
    assert (
        PlannerAgent._normalize_intent(
            "",
            "给我10篇考研11408上岸的热帖",
        )
        == "crawl_only"
    )


def test_planner_copywriting_only_creates_topic_from_seed():
    state = PipelineState(
        run_id="planner-copywriting-topic",
        user_message="帮我写一篇防晒霜推荐文案",
    )
    state.metadata["has_explicit_user_message"] = True
    llm = StaticLLM(
        _base_plan(
            intent="copywriting_only",
            topic_seed="防晒霜推荐",
            needs_crawl=False,
            needs_analysis=False,
            needs_topic_generation=False,
            needs_content_generation=True,
            needs_review=False,
            search_query="防晒霜推荐文案",
            search_keywords=["防晒霜", "防晒推荐"],
        )
    )

    result = asyncio.run(PlannerAgent(llm).run(state))

    assert result.failed is False
    assert result.plan.intent == "copywriting_only"
    assert result.plan.needs_content_generation is True
    assert result.plan.planned_stages[0].stage == PipelineStage.CRAWLING
    assert result.topics
    assert result.topics[0].title == "防晒霜推荐"


def test_planner_publish_post_does_not_require_account_or_schedule_for_mvp():
    state = PipelineState(
        run_id="planner-publish-post",
        user_message="先找最近的减脂餐热帖，然后帮我写一篇完整的并发出来",
    )
    state.metadata["has_explicit_user_message"] = True
    llm = StaticLLM(
        _base_plan(
            intent="publish_post",
            topic_seed="减脂餐",
            needs_publish=True,
            search_query="减脂餐热帖",
            search_keywords=["减脂餐", "减脂餐热帖"],
        )
    )

    result = asyncio.run(PlannerAgent(llm).run(state))

    assert result.failed is False
    assert result.plan.intent == "publish_post"
    assert result.plan.needs_clarification is False
    assert result.plan.needs_content_generation is True
    assert result.plan.needs_publish is True


def test_planner_treats_job_channel_request_as_sufficient_topic():
    state = PipelineState(
        run_id="planner-job-channel",
        user_message="\u5199\u4e00\u7bc7\u5f53\u4ee3\u5e74\u8f7b\u4eba\u627e\u5de5\u4f5c\u6e20\u9053\u7684\u5e16\u5b50",
    )
    state.metadata["has_explicit_user_message"] = True
    llm = StaticLLM(
        _base_plan(
            intent="clarify_request",
            needs_clarification=True,
            clarification_question="还需要补充主题方向。",
            clarification_fields=["topic"],
            clarification_tips="信息越完整，生成质量越高。",
            topic_seed="",
            search_query="",
            search_keywords=[],
        )
    )

    result = asyncio.run(PlannerAgent(llm).run(state))

    assert result.failed is False
    assert result.plan.needs_clarification is False
    assert result.plan.intent == "full_post"
    assert result.plan.topic_seed == "\u5f53\u4ee3\u5e74\u8f7b\u4eba\u627e\u5de5\u4f5c\u6e20\u9053"
    assert result.plan.needs_crawl is True
    assert result.plan.needs_analysis is True
    assert result.plan.needs_topic_generation is True
    assert result.plan.needs_content_generation is True


def test_planner_copywriting_uses_defaults_without_clarifying_for_audience_or_tone():
    state = PipelineState(
        run_id="planner-copywriting-defaults",
        user_message="\u5e2e\u6211\u5199\u4e00\u7bc7\u9632\u6652\u971c\u63a8\u8350\u6587\u6848",
        audience="",
        tone="",
    )
    state.metadata["has_explicit_user_message"] = True
    llm = StaticLLM(
        _base_plan(
            intent="copywriting_only",
            topic_seed="\u9632\u6652\u971c\u63a8\u8350",
            audience="",
            tone="",
            needs_crawl=False,
            needs_analysis=False,
            needs_topic_generation=False,
            needs_content_generation=True,
            needs_review=False,
            search_query="\u9632\u6652\u971c\u63a8\u8350\u6587\u6848",
            search_keywords=["\u9632\u6652\u971c", "\u9632\u6652\u63a8\u8350"],
        )
    )

    result = asyncio.run(PlannerAgent(llm).run(state))

    assert result.failed is False
    assert result.plan.needs_clarification is False
    assert result.plan.intent == "copywriting_only"
    assert result.plan.topic_seed == "\u9632\u6652\u971c\u63a8\u8350"
