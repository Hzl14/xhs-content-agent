"""
routes_feishu.py
椋炰功澶氱淮琛ㄦ牸鍚屾璺敱锛屼笌鍙戝竷娴佺▼瀹屽叏鐙珛銆?
  POST /feishu/sync              鐩存帴浼?XHSMCPToolArgs 鍚屾鍒伴涔?  POST /feishu/sync-from-agent   浼?agent/run 缁撴灉 + 鍥剧墖璺緞鍚屾鍒伴涔?"""

from fastapi import APIRouter, HTTPException

from legacy_app.models.schemas import (
    FeishuSyncRequest,
    FeishuSyncFromAgentRequest,
    FeishuSyncResponse,
    FeishuCrawledSyncRequest,
    XHSMCPToolArgs,
)
from legacy_app.services.feishu_service import sync_to_feishu, sync_crawled_notes_to_feishu
from legacy_app.services.publish_service import build_mcp_tool_args

router = APIRouter(prefix="/feishu", tags=["Feishu"])


@router.post("/sync", response_model=FeishuSyncResponse, summary="鍚屾绗旇鍒伴涔︼紙鐩存帴浼?MCP 鍙傛暟锛?)
async def feishu_sync(req: FeishuSyncRequest) -> FeishuSyncResponse:
    """
    灏嗕竴鏉＄瑪璁扮殑 MCP 鍙傛暟锛堜笌鍙戝竷灏忕孩涔︽椂瀹屽叏涓€鑷寸殑瀛楁锛夊啓鍏ラ涔﹀缁磋〃鏍笺€?    閫傚悎鍦?/publish/prepare 棰勮鍚庢墜鍔ㄥ喅瀹氭槸鍚﹀綊妗ｃ€?    """
    result = await sync_to_feishu(req.mcp_args, req.content_type)
    return FeishuSyncResponse(**result)


@router.post("/sync-from-agent", response_model=FeishuSyncResponse, summary="鍚屾绗旇鍒伴涔︼紙浠?agent/run 缁撴灉锛?)
async def feishu_sync_from_agent(req: FeishuSyncFromAgentRequest) -> FeishuSyncResponse:
    """
    鐩存帴鎶?/agent/run 鐨勫畬鏁寸粨鏋?+ 宸茬敓鎴愮殑鍥剧墖璺緞浼犺繘鏉ワ紝缁勮 MCP 鍙傛暟鍚庡悓姝ュ埌椋炰功銆?    result_index / content_index 閫夋嫨鍏蜂綋鍝潯鍐呭锛岄粯璁ゅ悇鍙栫 0 鏉°€?    """
    results = req.agent_result.results
    if req.result_index >= len(results):
        raise HTTPException(status_code=400, detail=f"result_index 瓒婄晫锛屽叡 {len(results)} 涓瘽棰?)
    item = results[req.result_index]
    if req.content_index >= len(item.contents):
        raise HTTPException(status_code=400, detail=f"content_index 瓒婄晫锛屽叡 {len(item.contents)} 鏉″唴瀹?)

    content = item.contents[req.content_index]
    mcp_args = build_mcp_tool_args(
        content=content,
        image_paths=req.image_paths,
        is_original=req.is_original,
        visibility=req.visibility,
    )
    result = await sync_to_feishu(mcp_args, content.content_type)
    return FeishuSyncResponse(**result)


@router.post("/sync-crawled", response_model=FeishuSyncResponse, summary="鎵归噺鍚屾鐖彇鏁版嵁鍒伴涔?)
async def feishu_sync_crawled(req: FeishuCrawledSyncRequest) -> FeishuSyncResponse:
    """灏嗙埇鍙栧埌鐨勭瑪璁板垪琛ㄦ壒閲忓啓鍏ラ涔︾埇铏暟鎹〃锛團EISHU_TABLE_ID锛夈€?""
    result = await sync_crawled_notes_to_feishu(req.items)
    return FeishuSyncResponse(**result)
