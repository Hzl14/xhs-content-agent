from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from legacy_app.models.schemas import SearchCrawlRequest
from legacy_app.services.local_site_crawler_service import (
    NAV_TIMEOUT_MS,
    STATE_FILE,
    XHSCrawler,
    XHS_BASE,
)


async def inspect_card(crawler: XHSCrawler, context, card: dict) -> dict:
    note = await crawler._fetch_note_detail(context, card)
    if note is None:
        return {
            "card": card,
            "parsed": False,
            "reason": "_fetch_note_detail returned None",
        }

    return {
        "card": card,
        "parsed": True,
        "title": note.title,
        "content_preview": note.content[:200],
        "likes": note.likes,
        "favorites": note.favorites,
        "comments": note.comments,
        "tags": note.tags,
        "publish_time": note.publish_time,
        "content_type": note.content_type,
        "is_valid": crawler._is_valid(note),
    }


async def main() -> None:
    os.environ["DEBUG"] = "true"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    request = SearchCrawlRequest(
        keywords=["考研上岸"],
        topic_words=["考研上岸"],
        target_count=3,
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
        report: dict = {
            "links_found": len(links),
            "items": [],
        }

        for card in links[:3]:
            report["items"].append(await inspect_card(crawler, context, card))

        print(json.dumps(report, ensure_ascii=False, indent=2))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
