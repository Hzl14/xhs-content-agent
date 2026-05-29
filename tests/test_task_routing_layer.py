from api.handlers import (
    _candidate_index_from_feedback,
    _route_by_rules,
    _routing_context,
    _should_execute_route,
)


def test_candidate_selection_rule_requires_candidates():
    context = _routing_context(active_generation=None)

    decision = _route_by_rules("第2篇", context)

    assert decision is not None
    assert decision.action == "new_task"


def test_candidate_selection_rule_uses_active_candidate_count():
    active_generation = {
        "status": "awaiting_selection",
        "candidates": [
            {"index": 1, "content": {}},
            {"index": 2, "content": {}},
        ],
    }
    context = _routing_context(active_generation=active_generation)

    decision = _route_by_rules("第2篇", context)

    assert decision is not None
    assert decision.action == "select_candidate"
    assert decision.selected_index == 2
    assert decision.source == "rule"


def test_fresh_generation_request_does_not_select_stale_candidate():
    active_generation = {
        "status": "awaiting_selection",
        "candidates": [
            {"index": 1, "content": {}},
            {"index": 2, "content": {}},
        ],
    }
    context = _routing_context(active_generation=active_generation)

    decision = _route_by_rules("写一篇当代年轻人找工作渠道的帖子", context)

    assert decision is not None
    assert decision.action == "new_task"
    assert decision.reason == "matched_fresh_task_request"


def test_counted_hot_post_request_does_not_select_stale_candidate():
    active_generation = {
        "status": "awaiting_selection",
        "candidates": [
            {"index": 1, "content": {}},
            {"index": 2, "content": {}},
            {"index": 10, "content": {}},
        ],
    }
    context = _routing_context(active_generation=active_generation)

    decision = _route_by_rules("给我10篇考研11408上岸的热帖", context)

    assert decision is not None
    assert decision.action == "new_task"
    assert decision.reason == "matched_fresh_task_request"
    assert decision.selected_index is None


def test_plain_one_article_is_not_candidate_index():
    assert _candidate_index_from_feedback("写一篇当代年轻人找工作渠道的帖子") is None
    assert _candidate_index_from_feedback("第一篇") == 1
    assert _candidate_index_from_feedback("第2篇") == 2


def test_publish_confirmation_rule_requires_publish_requested():
    context = _routing_context(active_generation={"status": "awaiting_selection", "candidates": []})

    decision = _route_by_rules("发", context)

    assert decision is None


def test_publish_confirmation_rule_requires_state():
    context = _routing_context(
        active_generation={
            "status": "awaiting_publish_confirmation",
            "publish_requested": True,
            "candidates": [{"index": 1, "content": {}}],
        }
    )

    decision = _route_by_rules("发", context)

    assert decision is not None
    assert decision.action == "confirm_publish"
    assert decision.confidence == 1.0


def test_explicit_publish_confirmation_can_select_candidate():
    context = _routing_context(
        active_generation={
            "status": "awaiting_selection",
            "candidates": [{"index": 1, "content": {}}],
        }
    )

    decision = _route_by_rules("确认发布第1篇", context)

    assert decision is not None
    assert decision.action == "confirm_publish"
    assert decision.selected_index == 1


def test_candidate_confirmation_is_not_only_selection():
    context = _routing_context(
        active_generation={
            "status": "awaiting_selection",
            "candidates": [{"index": 1, "content": {}}],
        }
    )

    decision = _route_by_rules("第1篇可以，就用这个", context)

    assert decision is not None
    assert decision.action == "confirm_active_generation"
    assert decision.selected_index == 1


def test_action_thresholds_are_stricter_for_publish_than_revision():
    assert not _should_execute_route("confirm_publish", 0.89)
    assert _should_execute_route("revise_active_generation", 0.70)
