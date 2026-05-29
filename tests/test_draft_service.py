import json

from models.evaluation import ReviewCritique
from models.schemas import ContentItem, GeneratedTopicWithContents, PipelineState, TopicItem
from services.draft_service import DraftService


def test_draft_service_exports_json_and_markdown(tmp_path):
    service = DraftService(base_dir=tmp_path)
    state = PipelineState(
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        user_id="user-1",
        user_message="帮我写一篇小红书文案",
        results=[
            GeneratedTopicWithContents(
                topic=TopicItem(title="护肤避坑", reason="用户需要真实分享"),
                contents=[
                    ContentItem(
                        title="3个护肤避坑细节",
                        body="这是一段正文。",
                        hashtags=["护肤", "避坑", "小红书"],
                        cta="你踩过哪个坑?",
                        image_suggestion="拍一张桌面产品对比图",
                    )
                ],
                critique=ReviewCritique(total_score=88),
            )
        ],
    )

    package = service.save_pipeline_draft(state)

    assert package is not None
    assert package.content_count == 1
    assert package.json_url == "/drafts/session-1/task-1/run-1/draft.json"
    assert package.markdown_url == "/drafts/session-1/task-1/run-1/draft.md"

    payload = json.loads((tmp_path / "session-1" / "task-1" / "run-1" / "draft.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "session-1" / "task-1" / "run-1" / "draft.md").read_text(encoding="utf-8")

    assert payload["session_id"] == "session-1"
    assert payload["task_id"] == "task-1"
    assert payload["results"][0]["topic"]["title"] == "护肤避坑"
    assert "# 小红书草稿包" in markdown
    assert "标题：3个护肤避坑细节" in markdown
    assert "#护肤 #避坑 #小红书" in markdown


def test_draft_service_skips_empty_results(tmp_path):
    service = DraftService(base_dir=tmp_path)
    state = PipelineState(run_id="run-1", session_id="session-1", task_id="task-1")

    assert service.save_pipeline_draft(state) is None
