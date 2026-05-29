"""
feishu_service.py
灏?AI 鐢熸垚鐨勫皬绾功绗旇锛堝惈鏈湴鍥剧墖璺緞锛夊悓姝ュ埌椋炰功澶氱淮琛ㄦ牸銆?瀛楁涓?XHSMCPToolArgs 瀵归綈锛屾柟渚挎牳瀵瑰疄闄呭彂甯冨弬鏁般€?
椋炰功琛ㄥ瓧娈碉紙闇€鍦ㄥ缁磋〃鏍间腑鎻愬墠寤哄ソ鍚屽悕鍒楋級锛?  鏍囬        鏂囨湰
  姝ｆ枃        鏂囨湰
  鏍囩        鏂囨湰
  鍥剧墖璺緞    鏂囨湰锛堝寮犵敤 | 鍒嗛殧锛?  鏄惁鍘熷垱    鏂囨湰锛堟槸 / 鍚︼級
  鍙鎬?     鏂囨湰
  鍐呭绫诲瀷    鏂囨湰
  鐢熸垚鏃堕棿    鏂囨湰
"""

import httpx
from datetime import datetime
from typing import List

from legacy_app.core.config import settings
from legacy_app.models.schemas import XHSMCPToolArgs, NoteItem


async def _get_tenant_access_token() -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(url, json={
            "app_id": settings.feishu_app_id,
            "app_secret": settings.feishu_app_secret,
        })
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"鑾峰彇椋炰功 token 澶辫触: {data}")
    return data["tenant_access_token"]


async def _create_record(token: str, table_id: str, fields: dict) -> dict:
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps"
        f"/{settings.feishu_app_token}/tables/{table_id}/records"
    )
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"fields": fields},
        )
    return resp.json()


def _build_fields(args: XHSMCPToolArgs, content_type: str = "") -> dict:
    """
    灏?XHSMCPToolArgs 杞垚椋炰功澶氱淮琛ㄦ牸瀛楁銆?    瀛楁鍚嶉』涓庨涔﹁〃澶村畬鍏ㄤ竴鑷淬€?    """
    fields: dict = {
        "鏍囬":     args.title,
        "姝ｆ枃":     args.content,
        "鏍囩":     " | ".join(args.tags) if args.tags else "",
        "鍥剧墖璺緞": " | ".join(args.images),
        "鏄惁鍘熷垱": "鏄? if args.is_original else "鍚?,
        "鍙鎬?:   args.visibility,
        "鐢熸垚鏃堕棿": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if content_type:
        fields["鍐呭绫诲瀷"] = content_type
    # 鍘绘帀绌哄瓧绗︿覆瀛楁
    return {k: v for k, v in fields.items() if v != ""}


async def sync_to_feishu(
    args: XHSMCPToolArgs,
    content_type: str = "",
) -> dict:
    """
    鎶婁竴鏉?AI 鐢熸垚鐨勭瑪璁板悓姝ュ埌椋炰功澶氱淮琛ㄦ牸銆?
    Args:
        args:         XHSMCPToolArgs锛屼笌鍙戝竷鍒板皬绾功鐨勫弬鏁板畬鍏ㄤ竴鑷?        content_type: 鍐呭绫诲瀷锛堟祴璇?娓呭崟/鏁欑▼/閬块浄/鍒嗕韩锛夛紝鏉ヨ嚜 ContentItem

    Returns:
        {"success": bool, "message": str}
    """
    if not settings.feishu_app_id or not settings.feishu_publish_table_id:
        return {"success": False, "message": "椋炰功閰嶇疆鏈～鍐欙紙FEISHU_APP_ID / FEISHU_PUBLISH_TABLE_ID锛?}

    try:
        token = await _get_tenant_access_token()
        fields = _build_fields(args, content_type)
        result = await _create_record(token, settings.feishu_publish_table_id, fields)
    except Exception as e:
        return {"success": False, "message": f"椋炰功鍚屾寮傚父: {e}"}

    if result.get("code") == 0:
        return {"success": True, "message": "椋炰功鍚屾鎴愬姛"}
    print(f"[Feishu] 鍚屾澶辫触锛屽畬鏁村搷搴? {result}")
    return {"success": False, "message": f"椋炰功鍚屾澶辫触: {result}"}


async def sync_crawled_notes_to_feishu(notes: List[NoteItem]) -> dict:
    """
    灏嗙埇鍙栧埌鐨勭瑪璁版壒閲忓悓姝ュ埌椋炰功鐖櫕鏁版嵁琛紙FEISHU_TABLE_ID锛夈€?    瀛楁涓?CrawlData_to_FeishiList.py 淇濇寔涓€鑷淬€?    """
    if not settings.feishu_app_id or not settings.feishu_table_id:
        return {"success": False, "message": "椋炰功閰嶇疆鏈～鍐欙紙FEISHU_APP_ID / FEISHU_TABLE_ID锛?}

    def _build_crawl_fields(note: NoteItem) -> dict:
        tags_text = " | ".join(note.tags) if note.tags else ""
        fields: dict = {
            "鏍囬": note.title or "",
            "浣滆€?: note.author or "",
            "姝ｆ枃": note.content or "",
            "閾炬帴": note.url or "",
            "鐐硅禐鏁?: note.likes,
            "璇勮鏁?: note.comments,
            "鏀惰棌鏁?: note.favorites,
            "鏍囩": tags_text,
            "鍙戝竷鏃堕棿": note.publish_time or "",
            "鍐呭绫诲瀷": note.content_type or "",
        }
        return {k: v for k, v in fields.items() if v != "" and v is not None}

    try:
        token = await _get_tenant_access_token()
    except Exception as e:
        return {"success": False, "message": f"鑾峰彇椋炰功 token 澶辫触: {e}"}

    success_count, fail_count = 0, 0
    for note in notes:
        try:
            fields = _build_crawl_fields(note)
            result = await _create_record(token, settings.feishu_table_id, fields)
            if result.get("code") == 0:
                success_count += 1
            else:
                fail_count += 1
        except Exception:
            fail_count += 1

    return {
        "success": fail_count == 0,
        "message": f"鍚屾瀹屾垚锛氭垚鍔?{success_count} 鏉★紝澶辫触 {fail_count} 鏉?,
    }
