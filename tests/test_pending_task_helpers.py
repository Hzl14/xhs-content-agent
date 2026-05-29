from api.handlers import (
    _is_skip_material_reply,
    _looks_like_new_task_while_pending,
    _merge_pending_user_message,
    _pending_task_payload,
    _requested_source_note_count,
    _should_ask_for_material,
)
from models.schemas import PipelineState


def test_merge_pending_user_message_keeps_original_task_context():
    pending = {"user_message": "帮我写一篇护肤小红书文案"}

    merged = _merge_pending_user_message(pending, "面向大学生，语气真实一点")

    assert "帮我写一篇护肤小红书文案" in merged
    assert "面向大学生，语气真实一点" in merged
    assert "[用户补充信息]" in merged


def test_pending_new_task_detection_is_explicit_only():
    assert not _looks_like_new_task_while_pending("面向大学生，语气真实一点")
    assert _looks_like_new_task_while_pending("换个主题，写考研经验")


def test_pending_task_payload_preserves_pipeline_state_identity():
    state = PipelineState(
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        user_message="帮我写一篇护肤小红书文案",
    )
    state.plan.clarification_question = "目标受众是谁？"
    state.plan.clarification_fields = ["audience"]

    payload = _pending_task_payload(state)

    assert payload["type"] == "clarification"
    assert payload["resume_mode"] == "replan"
    assert payload["run_id"] == "run-1"
    assert payload["session_id"] == "session-1"
    assert payload["task_id"] == "task-1"
    assert payload["pipeline_state"]["task_id"] == "task-1"
    assert payload["clarification_fields"] == ["audience"]


def test_material_skip_reply_detection():
    assert _is_skip_material_reply("没有素材，直接写")
    assert _is_skip_material_reply("跳过，你发挥")
    assert not _is_skip_material_reply("我去年投了80份简历")


def test_should_ask_for_material_only_for_material_dependent_modes():
    state = PipelineState(run_id="run-1", user_message="写一篇求职经验分享")
    state.metadata["has_user_material"] = False

    assert _should_ask_for_material(state, "personal_experience")
    assert _should_ask_for_material(state, "review_recommendation")
    assert not _should_ask_for_material(state, "analysis_guide")

    state.metadata["material_clarification_skipped"] = True
    assert not _should_ask_for_material(state, "personal_experience")


def test_requested_source_note_count_parses_collection_request():
    assert _requested_source_note_count("给我整理10个底层翻身的成功人士的帖子") == 10
    assert _requested_source_note_count("给我10篇考研11408上岸的热帖") == 10
    assert _requested_source_note_count("找5篇高质量热帖") == 5
    assert _requested_source_note_count("写一篇求职经验分享") is None
