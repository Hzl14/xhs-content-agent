from __future__ import annotations

import json
import re
from pathlib import Path

from models.schemas import DraftPackage, GeneratedTopicWithContents, PipelineState


class DraftService:
    def __init__(self, base_dir: str | Path = "data/output/drafts") -> None:
        self.base_dir = Path(base_dir)

    def save_pipeline_draft(self, state: PipelineState) -> DraftPackage | None:
        if not state.results:
            return None

        draft_id = self._safe_name(state.run_id)
        session_id = self._safe_name(state.session_id)
        task_id = self._safe_name(state.task_id)
        draft_dir = self.base_dir / session_id / task_id / draft_id
        draft_dir.mkdir(parents=True, exist_ok=True)

        payload = self._build_payload(state)
        json_path = draft_dir / "draft.json"
        markdown_path = draft_dir / "draft.md"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        markdown_path.write_text(self._build_markdown(state.results), encoding="utf-8")

        content_count = sum(len(block.contents) for block in state.results)
        relative = f"{session_id}/{task_id}/{draft_id}"
        return DraftPackage(
            draft_id=draft_id,
            json_path=str(json_path),
            markdown_path=str(markdown_path),
            json_url=f"/drafts/{relative}/draft.json",
            markdown_url=f"/drafts/{relative}/draft.md",
            content_count=content_count,
        )

    def _build_payload(self, state: PipelineState) -> dict:
        return {
            "draft_id": state.run_id,
            "session_id": state.session_id,
            "task_id": state.task_id,
            "run_id": state.run_id,
            "user_id": state.user_id,
            "user_message": state.user_message,
            "stage": state.stage,
            "failed": state.failed,
            "search_query": state.search_query,
            "search_keywords": state.search_keywords,
            "analysis_summary": state.analysis.summary if state.analysis else "",
            "results": [block.model_dump() for block in state.results],
            "metadata": {
                "publish_requested": state.metadata.get("publish_requested", False),
                "review_decision": state.metadata.get("review_decision"),
            },
        }

    @staticmethod
    def _build_markdown(results: list[GeneratedTopicWithContents]) -> str:
        lines: list[str] = ["# 小红书草稿包", ""]
        for block_index, block in enumerate(results, start=1):
            lines.extend(
                [
                    f"## 选题 {block_index}: {block.topic.title}",
                    "",
                    f"选题理由：{block.topic.reason}",
                    "",
                ]
            )
            if block.critique:
                lines.extend(
                    [
                        f"评分：{block.critique.total_score}",
                        "",
                    ]
                )
            for content_index, content in enumerate(block.contents, start=1):
                lines.extend(
                    [
                        f"### 草稿 {block_index}.{content_index}",
                        "",
                        f"标题：{content.title}",
                        "",
                        content.body.strip(),
                        "",
                    ]
                )
                if content.cta:
                    lines.extend(["CTA：", content.cta.strip(), ""])
                if content.hashtags:
                    lines.extend(["标签：", " ".join(f"#{tag}" for tag in content.hashtags if tag), ""])
                if content.image_suggestion:
                    lines.extend(["配图建议：", content.image_suggestion.strip(), ""])
                if content.content_type:
                    lines.extend([f"内容类型：{content.content_type}", ""])
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _safe_name(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
        return cleaned.strip("-") or "default"
