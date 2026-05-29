import asyncio

from agents.publisher_agent import PublisherAgent
from models.schemas import ContentItem, GeneratedTopicWithContents, PipelineState, TopicItem
from models.states import PipelineStage


def _publish_state(*, confirmed: bool = True, with_content: bool = True) -> PipelineState:
    results = []
    if with_content:
        results = [
            GeneratedTopicWithContents(
                topic=TopicItem(title="Topic", reason="Reason"),
                contents=[
                    ContentItem(
                        title="Draft title",
                        body="Draft body ready for mock publishing.",
                        hashtags=["tag1", "tag2"],
                        cta="What do you think?",
                        image_suggestion="Use one lifestyle image.",
                    )
                ],
            )
        ]
    return PipelineState(
        run_id="run-1",
        session_id="session-1",
        task_id="task-1",
        stage=PipelineStage.PUBLISHING,
        results=results,
        metadata={"publish_confirmed": confirmed, "selected_index": 1},
    )


def test_publisher_requires_user_confirmation():
    state = _publish_state(confirmed=False)

    result = asyncio.run(PublisherAgent().run(state))

    assert result.failed is True
    assert result.metadata["publish_status"] == "failed"
    assert "confirmation" in result.error_message


def test_publisher_requires_selected_content():
    state = _publish_state(with_content=False)

    result = asyncio.run(PublisherAgent().run(state))

    assert result.failed is True
    assert result.metadata["publish_status"] == "failed"
    assert "content item" in result.error_message


def test_publisher_creates_mock_publish_record():
    state = _publish_state()

    result = asyncio.run(PublisherAgent().run(state))

    assert result.failed is False
    assert result.metadata["publish_status"] == "simulated_success"
    assert result.metadata["publish_record"]["publish_id"].startswith("mock_")
    assert result.metadata["publish_record"]["selected_index"] == 1
    assert result.metadata["publish_payload"]["platform"] == "xhs"
    assert result.metadata["publish_payload"]["mode"] == "mock"
    assert result.metadata["publish_payload"]["title"] == "Draft title"
