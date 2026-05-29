"""
mcp_client_service.py
浣跨敤 MCP Python SDK锛圫treamable HTTP 浼犺緭锛変綔涓?MCP Client锛?璋冪敤鏈湴灏忕孩涔?MCP 鏈嶅姟锛坸iaohongshu-mcp锛夌殑宸ュ叿銆?
灏忕孩涔?MCP 鏈嶅姟绔偣锛歨ttp://localhost:18060/mcp
浼犺緭鍗忚锛歋treamable HTTP锛坢cp-go v0.7.0 瀹樻柟 SDK锛?
鍙敤宸ュ叿锛堥儴鍒嗭級锛?  - check_login_status   妫€鏌ョ櫥褰曠姸鎬?  - get_login_qrcode     鑾峰彇鐧诲綍浜岀淮鐮?  - publish_content      鍙戝竷鍥炬枃鍐呭  鈫?鏍稿績
  - list_feeds           鑾峰彇棣栭〉 feeds
  - search_feeds         鎼滅储鍐呭
"""

import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from legacy_app.core.config import settings
from legacy_app.models.schemas import XHSMCPToolArgs, SendPublishResponse


async def call_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """
    閫氱敤 MCP 宸ュ叿璋冪敤銆?
    Args:
        tool_name:  宸ュ叿鍚嶇О锛屽 "publish_content"
        arguments:  宸ュ叿鍙傛暟瀛楀吀

    Returns:
        宸ュ叿璋冪敤缁撴灉锛堣В鏋愬悗鐨?dict锛?    """
    async with streamablehttp_client(settings.xhs_mcp_endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments=arguments)

    # result.content 鏄?list[TextContent | ImageContent | ...]
    # publish_content 杩斿洖 TextContent锛屽唴瀹规槸 JSON 瀛楃涓?    if result.content:
        first = result.content[0]
        if hasattr(first, "text"):
            try:
                return json.loads(first.text)
            except json.JSONDecodeError:
                return {"raw": first.text}
    return {}


async def check_login_status() -> dict[str, Any]:
    """妫€鏌ュ皬绾功鐧诲綍鐘舵€侊紝杩斿洖 {logged_in: bool, username: str}"""
    return await call_tool("check_login_status", {})


async def list_mcp_tools() -> list[str]:
    """鍒楀嚭 MCP 鏈嶅姟绔敞鍐岀殑鎵€鏈夊伐鍏峰悕绉帮紙璋冭瘯鐢級"""
    async with streamablehttp_client(settings.xhs_mcp_endpoint) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_result = await session.list_tools()
    return [t.name for t in tools_result.tools]


async def publish_via_mcp(args: XHSMCPToolArgs) -> SendPublishResponse:
    """
    閫氳繃 MCP 鍗忚璋冪敤 publish_content 宸ュ叿鍙戝竷鍥炬枃鍐呭銆?
    Args:
        args: XHSMCPToolArgs锛屽瓧娈靛悕涓?MCP 宸ュ叿鍙傛暟涓€鑷达紙灏忓啓锛?
    Returns:
        SendPublishResponse
    """
    # 鏋勯€犲弬鏁板瓧鍏革紝鎺掗櫎 None 鍜岀┖鍒楄〃鐨勫彲閫夊瓧娈?    tool_args: dict[str, Any] = {
        "title": args.title,
        "content": args.content,
        "images": args.images,
        "is_original": args.is_original,
        "visibility": args.visibility,
    }
    if args.tags:
        tool_args["tags"] = args.tags
    if args.schedule_at:
        tool_args["schedule_at"] = args.schedule_at
    if args.products:
        tool_args["products"] = args.products

    try:
        result = await call_tool("publish_content", tool_args)
    except Exception as e:
        return SendPublishResponse(
            success=False,
            message=f"MCP 璋冪敤寮傚父: {e}",
            mode="mcp",
        )

    # XHS MCP 宸ュ叿涓嶄竴瀹氳繑鍥?{"success": true}锛?    # 鍙璋冪敤鏈姏寮傚父涓旇繑鍥炲唴瀹逛笉鍚槑纭敊璇爣蹇楋紝鍗宠涓烘垚鍔?    raw_text = result.get("raw", "")
    has_error = result.get("success") is False or "error" in raw_text.lower() or "澶辫触" in raw_text
    success = not has_error if raw_text else result.get("success", True)
    message = result.get("message") or raw_text or "鍙戝竷鎴愬姛"

    return SendPublishResponse(
        success=success,
        message=message,
        mode="mcp",
        data=result.get("data"),
    )
