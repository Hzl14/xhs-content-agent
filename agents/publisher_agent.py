from __future__ import annotations

import uuid
from typing import Any

from core.agent_base import BaseAgent
from models.schemas import ContentItem, NodeTrace, PipelineState, PublishRecord


class PublisherAgent(BaseAgent):
    name = "publisher"

    async def _execute(self, state: PipelineState, trace: NodeTrace) -> PipelineState:
        if not state.metadata.get("publish_confirmed"):
            return self._fail(state, trace, "PublisherAgent requires user publish confirmation.")

        content = self._first_content(state)
        if content is None:
            return self._fail(state, trace, "PublisherAgent requires one selected content item.")

        missing_fields = self._missing_required_fields(content)
        if missing_fields:
            return self._fail(
                state,
                trace,
                f"PublisherAgent content is missing required fields: {', '.join(missing_fields)}.",
            )

        publish_id = f"mock_{uuid.uuid4().hex[:12]}"
        selected_index = self._safe_int(state.metadata.get("selected_index"))
        record = PublishRecord(
            publish_id=publish_id,
            title=content.title,
            content_preview=content.body[:120],
            hashtags=content.hashtags,
            selected_index=selected_index,
            task_id=state.task_id,
            run_id=state.run_id,
        )

        state.metadata["publish_status"] = record.status
        state.metadata["publish_record"] = record.model_dump()
        state.metadata["publish_payload"] = {
            "platform": record.platform,
            "mode": record.mode,
            "title": content.title,
            "body": content.body,
            "cta": content.cta,
            "hashtags": content.hashtags,
            "image_suggestion": content.image_suggestion,
            "content_type": content.content_type,
            "selected_index": selected_index,
        }
        state.ai_message = f"Mock publish completed: {publish_id}"
        trace.status = "success"
        return state

    @staticmethod
    def _first_content(state: PipelineState) -> ContentItem | None:
        if not state.results:
            return None
        block = state.results[0]
        if not block.contents:
            return None
        return block.contents[0]

    @staticmethod
    def _missing_required_fields(content: ContentItem) -> list[str]:
        missing: list[str] = []
        if not content.title.strip():
            missing.append("title")
        if not content.body.strip():
            missing.append("body")
        if not content.cta.strip():
            missing.append("cta")
        return missing

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fail(state: PipelineState, trace: NodeTrace, message: str) -> PipelineState:
        state.failed = True
        state.error_message = message
        state.metadata["publish_status"] = "failed"
        trace.status = "failed"
        trace.error = message
        return state
