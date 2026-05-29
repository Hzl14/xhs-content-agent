"""
测试不同 URL 格式和 referrer 的详情页访问
"""
from __future__ import annotations
import asyncio, sys, io
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

# 上次爬到的真实 URL
SEARCH_RESULT_URL = "https://www.xiaohongshu.com/search_result/69ca5463000000002800bd05"
# 尝试转换为 /explore/ 格式
NOTE_ID = "69ca5463000000002800bd05"
EXPLORE_URL = f"https://www.xiaohongshu.com/explore/{NOTE_ID}"
STATE_FILE = "data/raw/xhs_state.json"
REFERER = "https://www.xiaohongshu.com/search_result?keyword=%E8%80%83%E7%A0%94%E4%B8%8A%E5%B2%B8%E7%BB%8F%E9%AA%8C%E8%B4%B4&source=web_explore_feed"


async def check_url(context, label: str, url: str, extra_headers: dict = None) -> str:
    page = await context.new_page()
    try:
        if extra_headers:
            await page.set_extra_http_headers(extra_headers)
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        final_url = page.url
        title = await page.title()
        content_loc = page.locator("#detail-desc")
        has_content = await content_loc.count() > 0
        content_text = ""
        if has_content:
            content_text = (await content_loc.first.inner_text())[:60]
        return f"[{label}]\n  最终URL: {final_url[:80]}\n  标题: {title[:50]}\n  有#detail-desc: {has_content}\n  内容预览: {content_text}"
    except Exception as e:
        return f"[{label}] 异常: {e}"
    finally:
        await page.close()


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        ctx_kwargs = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        }
        if Path(STATE_FILE).exists():
            ctx_kwargs["storage_state"] = STATE_FILE
        context = await browser.new_context(**ctx_kwargs)

        results = await asyncio.gather(
            check_url(context, "search_result URL 无 referer", SEARCH_RESULT_URL),
            check_url(context, "explore URL 无 referer", EXPLORE_URL),
            check_url(context, "search_result URL + Referer header", SEARCH_RESULT_URL,
                      {"Referer": REFERER}),
        )
        for r in results:
            print(r)
            print()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
