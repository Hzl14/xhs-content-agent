"""
MCP server entry for the refactored XHS agent.

Current scope:
- run_content_pipeline: execute the new pipeline and return structured result

This file is intentionally decoupled from crawler and publishing infrastructure.
"""

from mcp.server.fastmcp import FastMCP

from api.dependencies import get_container
from api.handlers import run_agent_pipeline
from models.schemas import AgentRunRequest


mcp = FastMCP(
    name="XHS Agent Refactored",
    instructions=(
        "Refactored Xiaohongshu content agent without LangChain. "
        "Use run_content_pipeline to generate topics and content with reflection."
    ),
)


@mcp.tool()
async def run_content_pipeline(
    audience: str = "大学生女性",
    tone: str = "真实分享",
    topic_count: int = 3,
    content_count_per_topic: int = 1,
) -> dict:
    container = get_container()
    request = AgentRunRequest(
        audience=audience,
        tone=tone,
        topic_count=topic_count,
        content_count_per_topic=content_count_per_topic,
    )
    result = await run_agent_pipeline(container, request)
    return result.model_dump()


if __name__ == "__main__":
    mcp.run()
