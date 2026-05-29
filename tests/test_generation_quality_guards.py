from core.evaluation_engine import EvaluationEngine
from core.text_postprocess import clean_content_item, has_markdown_leak
from models.schemas import ContentItem
from agents.content_agent import ContentAgent
from models.schemas import PipelineState


def _content(title: str, body: str, content_type: str = "分析指南") -> ContentItem:
    return ContentItem(
        title=title,
        body=body,
        hashtags=["恋爱观", "亲密关系", "成长"],
        cta="你觉得还有哪些常见误区？评论区聊聊",
        image_suggestion="关系议题文字封面",
        content_type=content_type,
    )


def test_clean_content_item_removes_markdown_syntax() -> None:
    content = _content(
        title="## 3个恋爱观误区",
        body="**第一**，把控制当安全感。\n\n可以沟通，但不要用冷暴力解决问题。",
    )

    cleaned = clean_content_item(content)

    assert not has_markdown_leak(f"{cleaned.title}\n{cleaned.body}\n{cleaned.cta}")
    assert cleaned.title == "3个恋爱观误区"
    assert "第一" in cleaned.body
    assert "**" not in cleaned.body


def test_markdown_leak_penalizes_format_score() -> None:
    engine = EvaluationEngine()
    clean = _content(
        title="3个恋爱观误区越早知道越好",
        body=(
            "第一，很多人把控制当成安全感，真正的问题不是爱不爱，而是边界有没有被尊重。\n\n"
            "第二，把随时秒回当成亲密关系，会让沟通变成压力，也会让彼此失去独立空间。\n\n"
            "第三，把情绪价值理解成单方面哄人，容易忽略双向支持和共同成长。"
        ),
    )
    leaked = clean.model_copy(update={"body": clean.body.replace("第一", "**第一**", 1)})

    assert engine._score_format(leaked) < engine._score_format(clean)


def test_analysis_authenticity_rewards_arguments_not_fake_personal_story() -> None:
    engine = EvaluationEngine()
    analysis_body = (
        "第一，恋爱观最大的误区，是把占有当成安全感。真正稳定的亲密关系，需要边界、尊重和沟通。\n\n"
        "第二，很多人把秒回当成爱，但本质上是在用控制感替代信任，这会消耗双方的独立空间。\n\n"
        "第三，健康关系不是永远不吵架，而是可以表达不同意见，也可以一起建立解决问题的方法。"
    )
    fake_story_body = (
        "我去年大三，分手后花了42天自救，每天写日记，后来突然明白恋爱观误区在哪里。\n\n"
        "我前任不回消息的时候，我就开始焦虑，最后靠三个方法走出来。\n\n"
        "这段经历让我知道，恋爱观真的会影响一个人。"
    )

    analysis_score = engine._score_authenticity(_content("3个恋爱观误区", analysis_body), analysis_body, None)
    fake_story_score = engine._score_authenticity(_content("22岁分手后42天", fake_story_body), fake_story_body, None)

    assert analysis_score >= 75
    assert fake_story_score < analysis_score


def test_material_detection_distinguishes_request_from_real_experience() -> None:
    assert not ContentAgent._has_user_material("写一篇求职经验分享，目标是大学生")
    assert not ContentAgent._has_user_material("没有素材，直接写")
    assert ContentAgent._has_user_material("我去年投了80份简历，最后拿到2个offer")


def test_personal_experience_request_enters_material_mode() -> None:
    state = PipelineState(run_id="run-1", user_message="写一篇我的求职经历分享，目标是大学生")

    assert ContentAgent._infer_content_mode(state, "我的求职经历分享", "") == "personal_experience"


def test_generic_experience_share_does_not_force_material_question() -> None:
    state = PipelineState(run_id="run-1", user_message="写一篇求职经验分享，目标是大学生")

    assert ContentAgent._infer_content_mode(state, "求职经验分享", "") == "general_guide"


def test_unverified_personal_story_is_detected() -> None:
    content = _content(
        title="22岁分手后42天自救",
        body="我去年大三，分手后花了42天自救，后来才知道真正的恋爱观是什么。",
    )

    assert ContentAgent._contains_unverified_personal_story(content)
