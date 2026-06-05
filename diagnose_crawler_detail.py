from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from playwright.async_api import async_playwright

from models.crawler_schemas import SearchCrawlRequest
from services.local_site_crawler_service import (
    DETAIL_PAGE_TIMEOUT_MS,
    NAV_TIMEOUT_MS,
    STATE_FILE,
    XHSCrawler,
    XHS_BASE,
)


async def main() -> None:
    os.environ["DEBUG"] = "true"
    request = SearchCrawlRequest(
        keywords=["考研上岸"],
        topic_words=["考研上岸"],
        target_count=1,
    )
    crawler = XHSCrawler(request)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs: dict = {
            "viewport": {"width": 1440, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        if Path(STATE_FILE).exists():
            context_kwargs["storage_state"] = str(STATE_FILE)

        context = await browser.new_context(**context_kwargs)
        page = await context.new_page()
        await page.goto(XHS_BASE, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await asyncio.sleep(2)

        links = await crawler._collect_card_links(page, "考研上岸")
        print(f"links_found={len(links)}")
        if not links:
            await browser.close()
            return

        card = links[0]
        detail_page = await context.new_page()
        await detail_page.goto(card["url"], wait_until="domcontentloaded", timeout=DETAIL_PAGE_TIMEOUT_MS)
        await asyncio.sleep(2)
        await crawler._dismiss_popups(detail_page)

        selectors = {
            "detail_desc": "#detail-desc",
            "bottom_date": ".bottom-container .date",
            "header_date": ".note-header .date",
            "generic_date": ".date",
            "detail_tag": "#detail-desc .tag",
            "hash_tag": "#hash-tag",
            "tag": ".tag",
            "meta_comment": "meta[name='og:xhs:note_comment']",
            "meta_like": "meta[name='og:xhs:note_like']",
            "meta_collect": "meta[name='og:xhs:note_collect']",
        }

        selector_counts: dict[str, int] = {}
        for name, selector in selectors.items():
            try:
                selector_counts[name] = await detail_page.locator(selector).count()
            except Exception:
                selector_counts[name] = -1

        title = await detail_page.title()
        content = await crawler._safe_text(detail_page, "#detail-desc")
        bottom_date = await crawler._safe_text(detail_page, ".bottom-container .date")
        header_date = await crawler._safe_text(detail_page, ".note-header .date")
        generic_date = await crawler._safe_text(detail_page, ".date")
        meta_comment = await crawler._meta_content(detail_page, "meta[name='og:xhs:note_comment']")
        meta_like = await crawler._meta_content(detail_page, "meta[name='og:xhs:note_like']")
        meta_collect = await crawler._meta_content(detail_page, "meta[name='og:xhs:note_collect']")

        dump = {
            "card": card,
            "title": title,
            "content_preview": (content or "")[:300],
            "bottom_date": bottom_date,
            "header_date": header_date,
            "generic_date": generic_date,
            "meta_comment": meta_comment,
            "meta_like": meta_like,
            "meta_collect": meta_collect,
            "selector_counts": selector_counts,
            "page_url": detail_page.url,
        }
        print(json.dumps(dump, ensure_ascii=False, indent=2))

        await detail_page.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
