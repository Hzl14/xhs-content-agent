from api.handlers import (
    _apply_delivery_route_to_plan,
    _clean_source_note_search_topic,
    _fallback_delivery_route,
    _normalize_delivery_route,
)
from models.schemas import PipelinePlan, PipelineState
from models.states import PipelineStage, PlannedStageStatus


def test_counted_hot_post_request_routes_to_source_notes():
    route = _fallback_delivery_route("给我10篇考研11408上岸的热帖")

    assert route["final_deliverable"] == "source_notes"
    assert route["intent"] == "crawl_only"
    assert route["count"] == 10


def test_rule_source_route_overrides_wrong_llm_draft_guess():
    route = _normalize_delivery_route(
        {
            "final_deliverable": "content_draft",
            "intent": "full_post",
            "topic": "考研11408上岸",
            "count": 0,
            "confidence": 0.9,
        },
        "给我10篇考研11408上岸的热帖",
    )

    assert route["final_deliverable"] == "source_notes"
    assert route["intent"] == "crawl_only"
    assert route["count"] == 10
    assert route["source"] == "rules"


def test_source_delivery_route_forces_crawl_only_plan():
    state = PipelineState(run_id="route-plan", user_message="给我整理10个底层翻身的成功人士的帖子")
    state.plan = PipelinePlan(
        intent="full_post",
        needs_crawl=True,
        needs_analysis=True,
        needs_topic_generation=True,
        needs_content_generation=True,
        needs_review=True,
        search_query="底层翻身 成功人士",
    )
    state.metadata["delivery_route"] = {
        "final_deliverable": "source_notes",
        "intent": "crawl_only",
        "topic": "底层翻身 成功人士",
        "count": 10,
        "confidence": 0.9,
    }

    _apply_delivery_route_to_plan(state)

    assert state.plan.intent == "crawl_only"
    assert state.plan.needs_crawl is True
    assert state.plan.needs_analysis is True
    assert state.plan.needs_topic_generation is False
    assert state.plan.needs_content_generation is False
    assert state.plan.needs_review is False
    assert state.final_note_limit >= 10
    assert state.raw_crawl_limit >= 20
    statuses = {item.stage: item.status for item in state.plan.planned_stages}
    assert statuses[PipelineStage.CRAWLING] == PlannedStageStatus.READY
    assert statuses[PipelineStage.CONTENT_GENERATING] == PlannedStageStatus.SKIPPED


def test_clean_source_note_search_topic_removes_request_words():
    assert _clean_source_note_search_topic("\u8fd4\u56de10\u7bc7\u8ba1\u7b97\u673a\u8003\u7814\u70ed\u5e16") == "\u8ba1\u7b97\u673a\u8003\u7814"
    assert _clean_source_note_search_topic("\u7ed9\u621110\u7bc7\u8003\u781411408\u4e0a\u5cb8\u7684\u70ed\u5e16") == "\u8003\u781411408\u4e0a\u5cb8"
    assert _clean_source_note_search_topic("\u722c\u53d65\u6761\u5b66\u4e60\u5fae\u8c03\u548c\u540e\u8bad\u7ec3\u7684\u9ad8\u8d28\u91cf\u5e16\u5b50") == "\u5fae\u8c03\u548c\u540e\u8bad\u7ec3"


def test_source_delivery_route_updates_actual_search_terms():
    state = PipelineState(run_id="route-clean-search", user_message="\u8fd4\u56de10\u7bc7\u8ba1\u7b97\u673a\u8003\u7814\u70ed\u5e16")
    state.plan = PipelinePlan(
        intent="full_post",
        needs_crawl=True,
        needs_analysis=True,
        needs_topic_generation=True,
        needs_content_generation=True,
        needs_review=True,
        search_query="\u8fd4\u56de10\u7bc7\u8ba1\u7b97\u673a\u8003\u7814\u70ed\u5e16",
        search_keywords=["\u8fd4\u56de10\u7bc7\u8ba1\u7b97\u673a\u8003\u7814"],
    )
    state.metadata["delivery_route"] = {
        "final_deliverable": "source_notes",
        "intent": "crawl_only",
        "topic": "10\u7bc7\u8ba1\u7b97\u673a\u8003\u7814\u70ed\u5e16",
        "count": 10,
        "confidence": 0.9,
    }

    _apply_delivery_route_to_plan(state)

    assert state.search_query == "\u8ba1\u7b97\u673a\u8003\u7814"
    assert state.search_keywords == ["\u8ba1\u7b97\u673a\u8003\u7814"]
    assert state.plan.search_query == "\u8ba1\u7b97\u673a\u8003\u7814"
    assert state.plan.search_keywords == ["\u8ba1\u7b97\u673a\u8003\u7814"]
