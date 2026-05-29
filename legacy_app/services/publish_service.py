"""
publish_service.py
1. 灏?ContentItem + 鍥剧墖璺緞鏁村悎鎴愪袱绉嶆牸寮忥細
   - XHSPublishPayload  (REST API 灞傦紝PascalCase 瀛楁)
   - XHSMCPToolArgs     (MCP 鍗忚灞傦紝灏忓啓瀛楁)
2. 鏀寔涓ょ鍙戝竷妯″紡锛?   - mode="mcp"   閫氳繃 MCP Streamable HTTP 鍗忚璋冪敤 publish_content 宸ュ叿
   - mode="rest"  鐩存帴璋冪敤 REST API POST /api/v1/publish
"""

import httpx

from legacy_app.core.config import settings
from legacy_app.models.schemas import (
    ContentItem,
    XHSPublishPayload,
    XHSMCPToolArgs,
    SendPublishResponse,
)


def _clean_tags(hashtags: list[str]) -> list[str]:
    """鍘婚櫎 # 鍓嶇紑銆佸幓閲嶃€佹渶澶?10 涓?""
    clean: list[str] = []
    seen: set[str] = set()
    for tag in hashtags:
        t = tag.lstrip("#").strip()
        if t and t not in seen:
            clean.append(t)
            seen.add(t)
        if len(clean) >= 10:
            break
    return clean


def build_xhs_payload(
    content: ContentItem,
    image_paths: list[str],
    is_original: bool = True,
    visibility: str = "鍏紑鍙",
) -> XHSPublishPayload:
    """
    缁勮 REST API 鏍煎紡锛圥ascalCase锛夌殑鍙戝竷 payload銆?    瀵瑰簲 POST /api/v1/publish銆?    """
    return XHSPublishPayload(
        Title=content.title[:20],
        Content=f"{content.body}\n\n{content.cta}",
        ImagePaths=image_paths,
        Tags=_clean_tags(content.hashtags),
        IsOriginal=is_original,
        Visibility=visibility,
    )


def build_mcp_tool_args(
    content: ContentItem,
    image_paths: list[str],
    is_original: bool = True,
    visibility: str = "鍏紑鍙",
) -> XHSMCPToolArgs:
    """
    缁勮 MCP 鍗忚鏍煎紡锛堝皬鍐欏瓧娈碉級鐨勫伐鍏峰弬鏁般€?    瀵瑰簲 MCP 宸ュ叿 publish_content 鐨勫弬鏁扮粨鏋勩€?    """
    return XHSMCPToolArgs(
        title=content.title[:20],
        content=f"{content.body}\n\n{content.cta}",
        images=image_paths,
        tags=_clean_tags(content.hashtags),
        is_original=is_original,
        visibility=visibility,
    )


async def send_via_rest(payload: XHSPublishPayload) -> SendPublishResponse:
    """
    REST 妯″紡锛氱洿鎺?POST /api/v1/publish锛岃烦杩?MCP 鍗忚灞傘€?    閫傚悎涓嶉渶瑕?MCP 鍗忚鐨勫満鏅垨璋冭瘯銆?    """
    url = f"{settings.xhs_mcp_url.rstrip('/')}/api/v1/publish"

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=payload.model_dump())

    if resp.status_code != 200:
        return SendPublishResponse(
            success=False,
            message=f"REST API 杩斿洖 HTTP {resp.status_code}: {resp.text}",
            mode="rest",
        )

    result = resp.json()
    return SendPublishResponse(
        success=result.get("success", False),
        message=result.get("message", ""),
        mode="rest",
        data=result.get("data"),
    )


async def send_to_xhs(
    content: ContentItem,
    image_paths: list[str],
    is_original: bool = True,
    visibility: str = "鍏紑鍙",
    mode: str = "mcp",
) -> SendPublishResponse:
    """
    缁熶竴鍙戝竷鍏ュ彛锛屾牴鎹?mode 閫夋嫨璋冪敤鏂瑰紡锛?      mode="mcp"   鈫?MCP Streamable HTTP 鍗忚锛堟帹鑽愶級
      mode="rest"  鈫?鐩存帴 REST API
    """
    if mode == "mcp":
        from legacy_app.services.mcp_client_service import publish_via_mcp
        mcp_args = build_mcp_tool_args(content, image_paths, is_original, visibility)
        return await publish_via_mcp(mcp_args)
    else:
        payload = build_xhs_payload(content, image_paths, is_original, visibility)
        return await send_via_rest(payload)
