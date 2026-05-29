"""
单个详情页调试脚本 - 检查并发失败原因
用法: .venv/Scripts/python test_detail.py
"""
from __future__ import annotations

import asyncio
import sys
import io
import random
import time
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")

TEST_URL = "https://www.xiaohongshu.com/search_result/69ca5463000000002800bd05"
STATE_FILE = "data/raw/xhs_state.json"


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        ctx_kwargs = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        if Path(STATE_FILE).exists():
            ctx_kwargs["storage_state"] = STATE_FILE
            print(f"已加载登录态: {STATE_FILE}")

        context = await browser.new_context(**ctx_kwargs)

        print("\n--- 测试1：主页是否需要重新登录 ---")
        page = await context.new_page()
        await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        title = await page.title()
        print(f"主页标题: {title}")
        url_now = page.url
        print(f"当前URL: {url_now}")
        if "login" in url_now or "登录" in title:
            print(">>> 登录态已失效，需重新扫码登录！")
            await page.close()
            await browser.close()
            return
        print("登录态有效")
        await page.close()

        print("\n--- 测试2：并发开 3 个详情页，看是否被重定向 ---")
        pages = []
        for i in range(3):
            pg = await context.new_page()
            pages.append(pg)

        results = await asyncio.gather(
            *[_open_detail(pg, TEST_URL, i) for i, pg in enumerate(pages)],
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"  页面{i}: 异常 {r}")
            else:
                print(f"  页面{i}: {r}")

        for pg in pages:
            try:
                await pg.close()
            except Exception:
                pass

        await browser.close()


async def _open_detail(page, url: str, idx: int) -> str:
    await asyncio.sleep(idx * random.uniform(1.0, 2.0))
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(2)
        title = await page.title()
        final_url = page.url
        # 检查是否被重定向到登录页
        if "login" in final_url or "sign" in final_url:
            return f"被重定向到登录页: {final_url}"
        content_loc = page.locator("#detail-desc")
        has_content = await content_loc.count() > 0
        return f"标题={title[:40]}  有内容={has_content}  URL={final_url[:60]}"
    except Exception as e:
        return f"异常: {e}"


if __name__ == "__main__":
    asyncio.run(main())
