from __future__ import annotations

import asyncio
import json
import os

from agents.crawler_agent import CrawlerAgent
from models.schemas import PipelineState
from services.storage_service import StorageService


async def main() -> None:
    # Avoid a conflicting system DEBUG env like "release".
    os.environ["DEBUG"] = "true"

    agent = CrawlerAgent(StorageService())
    state = PipelineState(
        run_id="crawler-smoke-test-utf8",
        user_id="local-test",
        user_message="帮我抓取关于考研上岸的小红书热帖",
        search_query="考研上岸",
        search_keywords=["考研上岸"],
        raw_crawl_limit=1,
        final_note_limit=1,
    )
    state = await agent.run(state)

    result = {
        "failed": state.failed,
        "error_message": state.error_message,
        "candidate_note_count": len(state.candidate_notes),
        "input_note_count": len(state.input_notes),
        "metadata": state.metadata,
        "notes": [
            {
                "title": note.title,
                "content_preview": note.content[:120],
                "likes": note.likes,
                "favorites": note.favorites,
                "comments": note.comments,
                "keyword_used": note.keyword_used,
                "keyword_type": note.keyword_type,
                "publish_time": note.publish_time,
                "url": note.url,
            }
            for note in state.input_notes
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
