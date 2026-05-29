"""
Smoke test script for the refactored pipeline crawler stage.

Usage:
  uv run python test_crawler.py
"""

import asyncio

from api.dependencies import get_container
from models.schemas import AgentRunRequest
from api.handlers import run_agent_pipeline


async def main() -> None:
    container = get_container()
    result = await run_agent_pipeline(
        container,
        AgentRunRequest(topic_count=1, content_count_per_topic=1),
    )
    print("run_id:", result.run_id)
    print("failed:", result.failed)
    print("topics:", len(result.results))


if __name__ == "__main__":
    asyncio.run(main())

