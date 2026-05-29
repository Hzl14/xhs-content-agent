"""
routes_publish.py
鍙戝竷鐩稿叧璺敱锛?
  POST /publish/prepare     鐢熸垚鍥剧墖 + 缁勮涓ょ鏍煎紡 payload锛堜笉瀹為檯鍙戝竷锛?  POST /publish/send        鍙戦€佸凡缁勮濂界殑 payload锛堟敮鎸?mcp / rest 妯″紡锛?  POST /publish/run         涓€姝ュ畬鎴愶細鐢熸垚鍥剧墖 鈫?缁勮 鈫?鍙戝竷
  GET  /publish/tools       鍒楀嚭 MCP 鏈嶅姟绔敞鍐岀殑鎵€鏈夊伐鍏凤紙璋冭瘯鐢級
  GET  /publish/login       妫€鏌ュ皬绾功 MCP 鐧诲綍鐘舵€?"""

from fastapi import APIRouter, HTTPException

from legacy_app.models.schemas import (
    PreparePublishRequest,
    PreparePublishResponse,
    SendPublishRequest,
    SendPublishResponse,
    AgentPublishRequest,
)
from legacy_app.services.image_service import generate_images
from legacy_app.services.publish_service import build_xhs_payload, build_mcp_tool_args, send_to_xhs

router = APIRouter(prefix="/publish", tags=["Publish"])


@router.post("/prepare", response_model=PreparePublishResponse, summary="鐢熸垚鍥剧墖骞剁粍瑁呭彂甯?payload")
async def prepare_publish(req: PreparePublishRequest) -> PreparePublishResponse:
    """
    1. 璋冪敤 gpt-image-1 鐢熸垚鍥剧墖锛屼繚瀛樺埌鏈湴
    2. 鍚屾椂缁勮 REST payload锛圥ascalCase锛夊拰 MCP tool args锛堝皬鍐欙級涓ょ鏍煎紡
    3. 杩斿洖缁撴灉锛?*涓嶅疄闄呭彂甯?*锛堜汉宸ョ‘璁ゅ悗鍐嶈皟 /publish/send 鎴?/publish/run锛?    """
    try:
        image_paths = await generate_images(
            topic=req.topic,
            content=req.content,
            image_count=req.image_count,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍥剧墖鐢熸垚澶辫触: {e}")

    rest_payload = build_xhs_payload(
        content=req.content,
        image_paths=image_paths,
        is_original=req.is_original,
        visibility=req.visibility,
    )
    mcp_args = build_mcp_tool_args(
        content=req.content,
        image_paths=image_paths,
        is_original=req.is_original,
        visibility=req.visibility,
    )

    return PreparePublishResponse(
        rest_payload=rest_payload,
        mcp_args=mcp_args,
        image_paths=image_paths,
    )


@router.post("/send", response_model=SendPublishResponse, summary="鍙戦€?payload 鍒?XHS MCP 鏈嶅姟")
async def send_publish(req: SendPublishRequest) -> SendPublishResponse:
    """
    灏?REST payload 鍙戦€佸埌 XHS MCP 鏈嶅姟銆?    - mode="mcp"  鈫?閫氳繃 MCP Streamable HTTP 鍗忚锛堝厛灏?PascalCase 杞垚灏忓啓鍙傛暟锛?    - mode="rest" 鈫?鐩存帴璋冪敤 REST API /api/v1/publish
    """
    # 浠?REST payload 鍙嶆帹 content 瀛楁鍐嶈蛋缁熶竴鍏ュ彛
    # 鍥犱负 SendPublishRequest 鎼哄甫鐨勬槸宸茬粍瑁呭ソ鐨?payload锛岀洿鎺ュ彂 REST 鏈€鐩存帴
    try:
        if req.mode == "rest":
            from legacy_app.services.publish_service import send_via_rest
            return await send_via_rest(req.payload)
        else:
            # MCP 妯″紡锛氬皢 PascalCase payload 杞垚 MCP tool args
            from legacy_app.services.mcp_client_service import publish_via_mcp
            from legacy_app.models.schemas import XHSMCPToolArgs
            mcp_args = XHSMCPToolArgs(
                title=req.payload.Title,
                content=req.payload.Content,
                images=req.payload.ImagePaths,
                tags=req.payload.Tags,
                is_original=req.payload.IsOriginal,
                visibility=req.payload.Visibility,
            )
            return await publish_via_mcp(mcp_args)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍙戝竷澶辫触: {e}")


@router.post("/run", response_model=SendPublishResponse, summary="涓€姝ュ畬鎴愶細鐢熸垚鍥剧墖 鈫?缁勮 鈫?鍙戝竷")
async def run_publish(req: PreparePublishRequest) -> SendPublishResponse:
    """
    瀹屾暣鍙戝竷娴佺▼锛?    1. gpt-image-1 鐢熸垚鍥剧墖
    2. 缁勮鍙戝竷鍙傛暟
    3. 鏍规嵁 mode 閫夋嫨 MCP 鎴?REST 鍙戝竷

    mode 榛樿 "mcp"锛屾帹鑽愪娇鐢?MCP 鍗忚銆?    """
    try:
        image_paths = await generate_images(
            topic=req.topic,
            content=req.content,
            image_count=req.image_count,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍥剧墖鐢熸垚澶辫触: {e}")

    try:
        result = await send_to_xhs(
            content=req.content,
            image_paths=image_paths,
            is_original=req.is_original,
            visibility=req.visibility,
            mode=req.mode,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍙戝竷澶辫触: {e}")

    if req.sync_feishu:
        mcp_args = build_mcp_tool_args(req.content, image_paths, req.is_original, req.visibility)
        result.feishu_sync = await sync_to_feishu(mcp_args, req.content.content_type)

    return result


@router.post("/run-from-agent", response_model=SendPublishResponse, summary="鐩存帴鐢?agent/run 鐨勭粨鏋滃彂甯?)
async def run_publish_from_agent(req: AgentPublishRequest) -> SendPublishResponse:
    """
    鎶?/agent/run 鐨勫畬鏁磋繑鍥炵洿鎺ヤ紶杩涙潵鍗冲彲锛屾棤闇€鎵嬪姩鎷煎瓧娈点€?    鐢?result_index 鍜?content_index 鎸囧畾鍙戝竷鍝釜璇濋涓嬬殑鍝潯鍐呭锛堥粯璁ゅ悇鍙栫 0 鏉★級銆?    """
    results = req.agent_result.results
    if req.result_index >= len(results):
        raise HTTPException(status_code=400, detail=f"result_index 瓒婄晫锛屽叡 {len(results)} 涓瘽棰?)
    item = results[req.result_index]
    if req.content_index >= len(item.contents):
        raise HTTPException(status_code=400, detail=f"content_index 瓒婄晫锛屽叡 {len(item.contents)} 鏉″唴瀹?)

    topic = item.topic
    content = item.contents[req.content_index]

    try:
        image_paths = await generate_images(
            topic=topic,
            content=content,
            image_count=req.image_count,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍥剧墖鐢熸垚澶辫触: {e}")

    try:
        result = await send_to_xhs(
            content=content,
            image_paths=image_paths,
            is_original=req.is_original,
            visibility=req.visibility,
            mode=req.mode,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"鍙戝竷澶辫触: {e}")

    return result


@router.get("/tools", summary="鍒楀嚭 MCP 鏈嶅姟绔墍鏈夋敞鍐屽伐鍏凤紙璋冭瘯鐢級")
async def list_tools() -> dict:
    """鍒楀嚭灏忕孩涔?MCP 鏈嶅姟绔敞鍐岀殑鎵€鏈夊伐鍏峰悕绉帮紝鐢ㄤ簬璋冭瘯纭杩炴帴姝ｅ父銆?""
    try:
        from legacy_app.services.mcp_client_service import list_mcp_tools
        tools = await list_mcp_tools()
        return {"tools": tools, "count": len(tools)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"杩炴帴 MCP 鏈嶅姟澶辫触: {e}")


@router.get("/login", summary="妫€鏌ュ皬绾功 MCP 鐧诲綍鐘舵€?)
async def check_login() -> dict:
    """閫氳繃 MCP 鍗忚妫€鏌ュ皬绾功鏄惁宸茬櫥褰曘€?""
    try:
        from legacy_app.services.mcp_client_service import check_login_status
        return await check_login_status()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"妫€鏌ョ櫥褰曠姸鎬佸け璐? {e}")
