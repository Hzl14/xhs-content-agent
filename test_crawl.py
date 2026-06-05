"""
独立爬虫测试脚本
用法: uv run python test_crawl.py
"""
from __future__ import annotations

import asyncio
import sys
import time

# 强制 UTF-8 输出，兼容 Windows GBK 终端
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")


async def main() -> None:
    from models.crawler_schemas import SearchCrawlRequest
    from services.local_site_crawler_service import crawl_local_site_notes

    keyword = "考研上岸经验贴"
    target = 25

    print("=== 爬虫测试 ===")
    print(f"关键词: {keyword}")
    print(f"目标条数: {target}")
    print("-" * 60)

    request = SearchCrawlRequest(
        keywords=[keyword],
        topic_words=[keyword],
        min_comments=0,
        min_likes=0,
        min_favorites=0,
        target_count=target,
    )

    t0 = time.monotonic()
    response = await crawl_local_site_notes(request)
    elapsed = time.monotonic() - t0

    print(f"\n=== 结果 ===")
    print(f"耗时: {elapsed:.1f}s")
    print(f"目标: {response.target_count}  实际: {response.count}")
    print(f"使用关键词: {response.used_keywords}")
    print()

    for i, note in enumerate(response.items, 1):
        print(f"[{i:02d}] {note.title[:55]}")
        print(f"      赞:{note.likes}  藏:{note.favorites}  评:{note.comments}")
        print(f"      发布: {note.publish_time or '未知'}  标签: {note.tags[:3]}")
        preview = (note.content or "").replace("\n", " ")[:100]
        print(f"      内容: {preview}")
        print(f"      URL: {(note.url or '无')[:80]}")
        print()

    # 质量评估
    items = response.items
    if items:
        avg_likes = sum(n.likes for n in items) / len(items)
        avg_favs = sum(n.favorites for n in items) / len(items)
        avg_comments = sum(n.comments for n in items) / len(items)
        has_content = sum(1 for n in items if len(n.content or "") >= 30)
        has_tags = sum(1 for n in items if n.tags)
        print("=== 质量评估 ===")
        print(f"平均点赞: {avg_likes:.0f}  平均收藏: {avg_favs:.0f}  平均评论: {avg_comments:.0f}")
        print(f"有正文(>=30字): {has_content}/{len(items)}")
        print(f"有标签: {has_tags}/{len(items)}")
        print()
        if len(items) < target:
            print(f"[提示] 仅爬取到 {len(items)}/{target} 条，可能原因：")
            print("  1. 登录态过期，需重新扫码登录")
            print("  2. 小红书限流，并发详情页被重定向")
            print("  3. 搜索结果中图文帖数量不足（视频帖被过滤）")
    else:
        print("未爬取到任何内容。")
        print("请确认 data/raw/xhs_state.json 登录态是否有效。")


if __name__ == "__main__":
    asyncio.run(main())
