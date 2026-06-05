from __future__ import annotations

import asyncio
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from playwright.async_api import BrowserContext, Page, async_playwright

from models.crawler_schemas import NoteItem, SearchCrawlRequest, SearchCrawlResponse
from utils.text_encoding import repair_mojibake


XHS_BASE = "https://www.xiaohongshu.com"
STATE_FILE = "data/raw/xhs_state.json"

VIDEO_TYPE_KEYWORDS = ["视频", "直播", "合集"]

MAX_TOTAL_SECONDS = 240       # 全局超时
PHASE1_SCROLL_ROUNDS = 3      # 搜索页滚动轮数（JS批量提取，3轮足够）
PHASE2_SCROLL_ROUNDS = 3      # Phase2 页面初始滚动，确保卡片全部加载
PHASE2_MAX_NOTES = 8          # 每个 Phase2 页面最多采集的详情数
PHASE2_PARALLEL_PAGES = 2     # Phase2 并发页面数
DETAIL_PAGE_TIMEOUT_MS = 12000
DETAIL_ENRICH_CONCURRENCY = 4
DETAIL_ENRICH_TOTAL_SECONDS = 90
NAV_TIMEOUT_MS = 25000

# Phase1 批量提取卡片数据的 JS 脚本（单次调用替代大量 Playwright 往返）
_JS_EXTRACT_CARDS = """
() => {
    const VIDEO_KW = ['视频', '直播', '合集'];
    const textOf = (el) => (el && (el.innerText || el.textContent || '') || '').trim();
    const attrOf = (el, names) => {
        if (!el) return '';
        for (const name of names) {
            const value = el.getAttribute(name);
            if (value && value.trim()) return value.trim();
        }
        return '';
    };
    const pickTitle = (card, link) => {
        const titleEl = card.querySelector(
            '.title,.note-title,span.title,div.title,[class*="title"],[data-testid*="title"]'
        );
        const image = card.querySelector('img');
        return (
            textOf(titleEl)
            || attrOf(link, ['title', 'aria-label'])
            || attrOf(image, ['alt', 'title'])
            || textOf(link)
        ).replace(/\\s+/g, ' ').slice(0, 100);
    };

    let cards = Array.from(document.querySelectorAll(
        'section.note-item,div.note-item,[class*="note-item"],[data-testid*="note"]'
    ));
    if (!cards.length) {
        cards = Array.from(document.querySelectorAll(
            'a[href*="/explore/"],a[href*="/search_result/"],a[href*="/discovery/item/"]'
        )).map(link => link.closest('section,article,div') || link);
    }

    return cards.map(card => {
        const link = card.matches && card.matches('a[href]') ? card : card.querySelector(
            'a.cover,a[href*="/explore/"],a[href*="/search_result/"],a[href*="/discovery/item/"]'
        );
        const author = card.querySelector('.author,[class*="author"],[class*="user"],[class*="nickname"]');
        const date = card.querySelector('.time,[class*="time"],[class*="date"]');
        const likes = card.querySelector('.like-wrapper .count,.likes-count,.interact-info .count,[class*="like"] [class*="count"],[class*="count"]');
        const tagArea = card.querySelector('.bottom-tag-area,[class*="tag"]');
        const cardText = textOf(card);
        const isVideo = !!(card.querySelector('.video-badge,.play-icon,.duration,[class*="video-mark"],[class*="play"]'))
            || VIDEO_KW.some(k => cardText.includes(k) || (tagArea && textOf(tagArea).includes(k)));
        return {
            href: link ? link.getAttribute('href') : null,
            title: pickTitle(card, link),
            author: author ? textOf(author).split('\\n')[0].trim() : null,
            date_text: date ? textOf(date) : null,
            likes_text: likes ? textOf(likes) : '0',
            is_video: isVideo
        };
    });
}
"""

# Phase2 获取当前搜索页所有卡片 href 列表（用于 go_back 后继续点击）
_JS_GET_HREFS = """
() => Array.from(document.querySelectorAll(
            'section.note-item a.cover,div.note-item a.cover,[class*="note-item"] a.cover,a[href*="/explore/"],a[href*="/search_result/"],a[href*="/discovery/item/"]'
         ))
         .map(el => el.getAttribute('href'))
         .filter(Boolean)
"""


class XHSCrawler:
    """
    两阶段爬虫：
      Phase 1 — 单次 JS 调用批量提取搜索页卡片摘要（标题/URL/日期/点赞）
      Phase 2 — 2 个页面并发，各自 goto 搜索页后用 go_back 循环点击详情，补全正文/标签/互动数
    """

    DETAIL_CONTENT_SELECTOR = "#detail-desc"
    DETAIL_DATE_SELECTORS = [".bottom-container .date", ".note-header .date", ".date"]
    DETAIL_TAG_SELECTORS = ["#detail-desc .tag", "#hash-tag", ".tag"]
    META_COMMENT = "meta[name='og:xhs:note_comment']"
    META_LIKE = "meta[name='og:xhs:note_like']"
    META_COLLECT = "meta[name='og:xhs:note_collect']"
    POPUP_CLOSE_SELECTORS = [".close-btn", ".modal-close", "[class*='close-icon']", "button.close"]
    LOGIN_HINT_SELECTORS = [
        "input[type='tel']",
        "input[placeholder*='手机号']",
        "input[placeholder*='验证码']",
        ".login-container",
    ]

    def __init__(self, request: SearchCrawlRequest):
        self.request = request
        self.request.keywords = [repair_mojibake(keyword) for keyword in self.request.keywords if keyword.strip()]
        self.request.topic_words = [repair_mojibake(word) for word in self.request.topic_words if word.strip()]
        self.seen_urls: set[str] = set()
        self.used_keywords: list[str] = []
        self.started_at = time.monotonic()

    # ── 主入口 ────────────────────────────────────────────────────────────────

    async def crawl(self) -> SearchCrawlResponse:
        state_path = Path(STATE_FILE)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx_kwargs: dict = {
                "viewport": {"width": 1440, "height": 900},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }
            if state_path.exists():
                ctx_kwargs["storage_state"] = str(state_path)
                print(f"[XHSCrawler] Loaded persisted login state: {STATE_FILE}")

            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            await self._ensure_logged_in(page, context)

            # ── Phase 1：JS 批量提取卡片摘要（速度快，约 15s）────────────────
            candidate_stubs: list[dict] = []
            for keyword in self.request.keywords:
                if self._total_timed_out():
                    break
                self.used_keywords.append(keyword)
                quota = max(10, self.request.target_count * 2 // max(len(self.request.keywords), 1))
                stubs = await self._phase1_collect_stubs(page, keyword, quota)
                candidate_stubs.extend(stubs)
                print(f"  [Phase1] keyword={keyword}  cards={len(stubs)}")

            candidate_stubs = self._deduplicate_stubs(candidate_stubs)
            print(f"[Phase1 done] {len(candidate_stubs)} stubs  elapsed={time.monotonic()-self.started_at:.0f}s")

            if str(getattr(self.request, "detail_mode", "all")).lower() == "none":
                await browser.close()
                final_notes = [self._stub_to_note(s) for s in candidate_stubs[: self.request.target_count]]
                elapsed = time.monotonic() - self.started_at
                print(f"[Done summary_only] notes={len(final_notes)}  elapsed={elapsed:.0f}s")
                return SearchCrawlResponse(
                    target_count=self.request.target_count,
                    count=len(final_notes),
                    used_keywords=self.used_keywords,
                    items=final_notes,
                )

            # ── Phase 2：2 个页面并发，go_back 循环点击详情 ───────────────────
            keywords = self.request.keywords or [candidate_stubs[0]["keyword"]] if candidate_stubs else []
            enriched = await self._phase2_parallel(context, keywords)
            print(f"[Phase2 done] enriched={len(enriched)}  elapsed={time.monotonic()-self.started_at:.0f}s")

            await browser.close()

        # Phase2 结果为主；若 Phase2 失败则降级为 Phase1 摘要
        final_notes = enriched if enriched else [self._stub_to_note(s) for s in candidate_stubs[: self.request.target_count]]
        elapsed = time.monotonic() - self.started_at
        print(f"[Done] notes={len(final_notes)}  elapsed={elapsed:.0f}s")

        return SearchCrawlResponse(
            target_count=self.request.target_count,
            count=len(final_notes),
            used_keywords=self.used_keywords,
            items=final_notes,
        )

    # ── Phase 1：JS 批量提取卡片摘要 ─────────────────────────────────────────

    async def _phase1_collect_stubs(self, page: Page, keyword: str, quota: int) -> list[dict]:
        search_url = f"{XHS_BASE}/search_result?keyword={quote(keyword)}&source=web_explore_feed"
        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            print(f"  [Phase1] goto failed: {e}")
            return []

        await asyncio.sleep(random.uniform(1.0, 1.5))
        await self._dismiss_popups(page)
        if await self._looks_logged_out(page):
            raise RuntimeError(
                "xiaohongshu_login_expired: search page is blocked by login or verification. "
                f"Refresh {STATE_FILE} and retry."
            )

        for _ in range(PHASE1_SCROLL_ROUNDS):
            await page.evaluate("window.scrollBy(0, 1400)")
            await asyncio.sleep(random.uniform(0.6, 0.9))

        # 单次 JS 调用获取所有卡片数据（大幅减少 Playwright 异步往返次数）
        try:
            cards_data: list[dict] = await page.evaluate(_JS_EXTRACT_CARDS)
        except Exception as e:
            print(f"  [Phase1] JS extract failed: {e}")
            return []
        if not cards_data and await self._looks_logged_out(page):
            raise RuntimeError(
                "xiaohongshu_login_expired: search page returned no cards because login is required. "
                f"Refresh {STATE_FILE} and retry."
            )

        stubs: list[dict] = []
        for card in cards_data:
            if len(stubs) >= quota:
                break
            try:
                if card.get("is_video"):
                    continue
                href = card.get("href")
                if not href:
                    continue
                url = href if href.startswith("http") else f"{XHS_BASE}{href}"
                if url in self.seen_urls:
                    continue

                card_date = self._normalize_date(card.get("date_text"))
                if card.get("date_text") and card_date and not self._is_within_one_year(card_date):
                    continue

                stubs.append({
                    "url": url,
                    "title": card.get("title", ""),
                    "author": card.get("author"),
                    "card_date": card_date,
                    "keyword": keyword,
                    "approx_likes": self._parse_number(card.get("likes_text", "0")),
                })
                self.seen_urls.add(url)
            except Exception:
                continue

        return stubs

    # ── Phase 2：并发详情页采集（go_back 循环，不重新加载搜索页）────────────

    async def _phase2_parallel(self, context, keywords: list[str]) -> list[NoteItem]:
        """启动 PHASE2_PARALLEL_PAGES 个页面，各自独立采集详情。"""
        if not keywords:
            return []

        # 将关键词均分给各并发页面
        pages = [await context.new_page() for _ in range(PHASE2_PARALLEL_PAGES)]
        assignments = [(pages[i % PHASE2_PARALLEL_PAGES], keywords[i % len(keywords)])
                       for i in range(PHASE2_PARALLEL_PAGES)]
        try:
            results = await asyncio.gather(
                *[self._phase2_page_worker(pg, kw) for pg, kw in assignments],
                return_exceptions=True,
            )
        finally:
            for pg in pages:
                try:
                    await pg.close()
                except Exception:
                    pass

        notes: list[NoteItem] = []
        seen_urls: set[str] = set()
        for r in results:
            if not isinstance(r, list):
                continue
            for note in r:
                key = note.url or note.title or ""
                if key and key not in seen_urls:
                    seen_urls.add(key)
                    notes.append(note)
        return notes

    async def _phase2_page_worker(self, page: Page, keyword: str) -> list[NoteItem]:
        """
        单页面 Phase2：goto 搜索页一次，按卡片序号顺序点击，go_back 后继续下一张。
        用序号而非 href 定位，避免 go_back 后页面滚回顶部找不到目标卡片。
        """
        search_url = f"{XHS_BASE}/search_result?keyword={quote(keyword)}&source=web_explore_feed"
        notes: list[NoteItem] = []

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await asyncio.sleep(random.uniform(1.0, 1.5))
            await self._dismiss_popups(page)

            for _ in range(PHASE2_SCROLL_ROUNDS):
                await page.evaluate("window.scrollBy(0, 1400)")
                await asyncio.sleep(random.uniform(0.6, 0.9))

            # 一次性获取所有卡片 href（记录 url，后续按序号点击）
            hrefs: list[str] = await page.evaluate(_JS_GET_HREFS)
        except Exception as e:
            print(f"  [Phase2] setup failed for '{keyword}': {e}")
            return notes

        card_index = 0  # 当前要点击的卡片序号
        for href in hrefs:
            if len(notes) >= PHASE2_MAX_NOTES or self._total_timed_out():
                break

            url = href if href.startswith("http") else f"{XHS_BASE}{href}"

            try:
                # 直接按序号点击，不依赖 href 定位（go_back 后序号不变）
                card_selector = f"section.note-item:nth-of-type({card_index + 1}) a.cover"
                link_loc = page.locator(card_selector).first

                # 若序号定位失败，退回 href 模糊匹配
                if not await link_loc.count():
                    note_id = url.split("/")[-1].split("?")[0]
                    link_loc = page.locator(f"a.cover[href*='{note_id}']").first
                    if not await link_loc.count():
                        card_index += 1
                        continue

                await link_loc.scroll_into_view_if_needed(timeout=3000)
                await asyncio.sleep(0.2)
                await link_loc.click()
                await page.wait_for_load_state("domcontentloaded", timeout=DETAIL_PAGE_TIMEOUT_MS)

                try:
                    await page.wait_for_selector(self.DETAIL_CONTENT_SELECTOR, timeout=5000)
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(0.5, 0.8))
                await self._dismiss_popups(page)

                if "website-login" in page.url or "error_code" in page.url:
                    await self._safe_go_back(page, search_url)
                    card_index += 1
                    continue

                current_url = page.url if "/explore/" in page.url else url
                stub = {"url": current_url, "keyword": keyword, "author": None, "card_date": None, "approx_likes": 0}
                note = await self._extract_detail(page, stub)
                if note and note.title:
                    notes.append(note)
                    preview = (note.title or "")[:30].encode("utf-8", errors="replace").decode("utf-8")
                    print(f"  + {preview}  likes={note.likes}", flush=True)

                await self._safe_go_back(page, search_url)
                card_index += 1

            except Exception as e:
                print(f"  [Phase2] card[{card_index}] error: {e}", flush=True)
                await self._safe_go_back(page, search_url)
                card_index += 1
                continue

        return notes

    async def _safe_go_back(self, page: Page, fallback_url: str) -> None:
        """go_back 失败时自动回退到搜索页。"""
        try:
            await page.go_back()
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
            await asyncio.sleep(0.3)
        except Exception:
            try:
                await page.goto(fallback_url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
                await asyncio.sleep(0.5)
            except Exception:
                pass

    async def _fetch_detail_by_url(self, page: Page, note: NoteItem) -> NoteItem | None:
        if not note.url:
            return note
        try:
            await page.goto(note.url, wait_until="domcontentloaded", timeout=DETAIL_PAGE_TIMEOUT_MS)
            try:
                await page.wait_for_selector(self.DETAIL_CONTENT_SELECTOR, timeout=5000)
            except Exception:
                pass
            await asyncio.sleep(random.uniform(0.5, 0.8))
            await self._dismiss_popups(page)

            if "website-login" in page.url or "error_code" in page.url:
                return None

            current_url = page.url if "/explore/" in page.url else note.url
            stub = {
                "url": current_url,
                "title": note.title,
                "keyword": note.keyword_used or (self.request.keywords[0] if self.request.keywords else ""),
                "author": note.author,
                "card_date": note.publish_time,
                "approx_likes": note.likes,
            }
            return await self._extract_detail(page, stub)
        except Exception:
            return None

    async def enrich_note_details(
        self,
        notes: list[NoteItem],
        concurrency: int = DETAIL_ENRICH_CONCURRENCY,
        total_timeout: int = DETAIL_ENRICH_TOTAL_SECONDS,
    ) -> list[NoteItem]:
        if not notes:
            return []

        state_path = Path(STATE_FILE)
        started = time.monotonic()
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx_kwargs: dict = {
                "viewport": {"width": 1440, "height": 900},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }
            if state_path.exists():
                ctx_kwargs["storage_state"] = str(state_path)

            context = await browser.new_context(**ctx_kwargs)
            login_page = await context.new_page()
            await self._ensure_logged_in(login_page, context)
            await login_page.close()

            queue: asyncio.Queue[tuple[int, NoteItem]] = asyncio.Queue()
            for index, note in enumerate(notes):
                queue.put_nowait((index, note))

            results: list[NoteItem | None] = [None] * len(notes)

            async def worker() -> None:
                page = await context.new_page()
                try:
                    while not queue.empty() and (time.monotonic() - started) < total_timeout:
                        try:
                            index, note = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        enriched = await self._fetch_detail_by_url(page, note)
                        results[index] = enriched or note
                        queue.task_done()
                finally:
                    await page.close()

            worker_count = max(1, min(concurrency, len(notes)))
            tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
            try:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=total_timeout)
            except asyncio.TimeoutError:
                for task in tasks:
                    task.cancel()
            finally:
                await browser.close()

        return [result or notes[index] for index, result in enumerate(results)]

    # ── 详情页内容提取 ────────────────────────────────────────────────────────

    async def _extract_detail(self, page: Page, stub: dict) -> Optional[NoteItem]:
        try:
            raw_title = await page.title()
            title = re.sub(r"\s*[-—–|]\s*小红书\s*$", "", raw_title).strip()
            title = title.split("\n")[0].strip()[:100] or stub.get("title", "")

            content = await self._safe_text(page, self.DETAIL_CONTENT_SELECTOR) or ""
            date_raw = await self._safe_text_candidates(page, self.DETAIL_DATE_SELECTORS)
            comments_text = await self._meta_content(page, self.META_COMMENT)
            likes_text = await self._meta_content(page, self.META_LIKE)
            favorites_text = await self._meta_content(page, self.META_COLLECT)

            tags: list[str] = []
            for selector in self.DETAIL_TAG_SELECTORS:
                try:
                    loc = page.locator(selector)
                    if await loc.count() > 0:
                        tags = [t.strip() for t in await loc.all_inner_texts() if t.strip()]
                        break
                except Exception:
                    continue

            return NoteItem(
                title=title,
                content=content.strip(),
                author=stub.get("author"),
                comments=self._parse_number(comments_text),
                likes=self._parse_number(likes_text) or stub.get("approx_likes", 0),
                favorites=self._parse_number(favorites_text),
                tags=tags,
                publish_time=self._normalize_date(date_raw) or stub.get("card_date"),
                url=stub["url"],
                content_type="图文",
                keyword_used=stub["keyword"],
            )
        except Exception:
            return None

    # ── 工具方法 ──────────────────────────────────────────────────────────────

    def _total_timed_out(self) -> bool:
        return (time.monotonic() - self.started_at) >= MAX_TOTAL_SECONDS

    def _deduplicate_stubs(self, stubs: list[dict]) -> list[dict]:
        seen: set[str] = set()
        result: list[dict] = []
        for s in stubs:
            key = s.get("url") or s.get("title") or ""
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(s)
        return result

    def _stub_to_note(self, stub: dict) -> NoteItem:
        return NoteItem(
            title=stub.get("title") or "",
            content="",
            author=stub.get("author"),
            likes=stub.get("approx_likes", 0),
            publish_time=stub.get("card_date"),
            url=stub.get("url"),
            keyword_used=stub.get("keyword"),
        )

    def _is_within_one_year(self, publish_time: Optional[str]) -> bool:
        if not publish_time:
            return False
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                dt = datetime.strptime(publish_time.strip(), fmt)
                return dt >= datetime.now() - timedelta(days=365)
            except ValueError:
                continue
        return False

    async def _ensure_logged_in(self, page: Page, context: BrowserContext) -> None:
        await page.goto(XHS_BASE, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await asyncio.sleep(2)
        if Path(STATE_FILE).exists():
            if not await self._looks_logged_out(page):
                print("[XHSCrawler] Reusing persisted login state.")
                return
            print("[XHSCrawler] Persisted login state appears expired.")
            raise RuntimeError(
                "xiaohongshu_login_expired: persisted login state is no longer valid. "
                "Click the Xiaohongshu login button and scan the QR code again, "
                f"then retry. State file: {STATE_FILE}."
            )
        raise RuntimeError(
            "xiaohongshu_login_required: no persisted login state found. "
            f"Click the Xiaohongshu login button to create {STATE_FILE}."
        )

    async def _looks_logged_out(self, page: Page) -> bool:
        if "website-login" in page.url:
            return True
        try:
            body_text = await page.locator("body").inner_text(timeout=2000)
        except Exception:
            body_text = ""
        logged_out_texts = [
            "登录后查看更多",
            "扫码登录",
            "验证码登录",
            "手机登录",
            "登录后即可查看",
        ]
        if any(text in body_text for text in logged_out_texts):
            return True
        for selector in self.LOGIN_HINT_SELECTORS:
            try:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    async def _dismiss_popups(self, page: Page) -> None:
        for selector in self.POPUP_CLOSE_SELECTORS:
            try:
                btn = page.locator(selector).first
                if await btn.count() > 0:
                    await btn.click()
                    await asyncio.sleep(0.2)
                    return
            except Exception:
                continue

    async def _meta_content(self, page: Page, selector: str) -> Optional[str]:
        try:
            loc = page.locator(selector)
            if await loc.count() > 0:
                return await loc.first.get_attribute("content")
        except Exception:
            pass
        return None

    async def _safe_text(self, element, selector: str) -> Optional[str]:
        try:
            loc = element.locator(selector).first
            if await loc.count() > 0:
                text = await loc.inner_text()
                return text.strip() if text and text.strip() else None
        except Exception:
            pass
        return None

    async def _safe_text_candidates(self, element, selectors: list[str]) -> Optional[str]:
        for selector in selectors:
            text = await self._safe_text(element, selector)
            if text:
                return text
        return None

    def _normalize_date(self, text: Optional[str]) -> Optional[str]:
        if not text:
            return None
        text = text.strip()
        matched = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", text)
        if matched:
            return matched.group(1).replace("/", "-")
        matched = re.search(r"(\d+)天前", text)
        if matched:
            return (datetime.now() - timedelta(days=int(matched.group(1)))).strftime("%Y-%m-%d")
        if "小时前" in text or "分钟前" in text or "刚刚" in text:
            return datetime.now().strftime("%Y-%m-%d")
        matched = re.search(r"(\d{1,2})-(\d{1,2})", text)
        if matched:
            return f"{datetime.now().year}-{matched.group(1).zfill(2)}-{matched.group(2).zfill(2)}"
        return None

    def _parse_number(self, text: Optional[str]) -> int:
        if not text:
            return 0
        text = text.strip().replace(",", "")
        lowered = text.lower()
        if "w" in lowered or "万" in text:
            matched = re.search(r"(\d+(?:\.\d+)?)", lowered)
            if matched:
                return int(float(matched.group(1)) * 10000)
        if "k" in lowered:
            matched = re.search(r"(\d+(?:\.\d+)?)", lowered)
            if matched:
                return int(float(matched.group(1)) * 1000)
        matched = re.search(r"(\d+)", text)
        return int(matched.group(1)) if matched else 0


async def crawl_local_site_notes(request: SearchCrawlRequest) -> SearchCrawlResponse:
    crawler = XHSCrawler(request)
    return await crawler.crawl()


async def enrich_local_site_note_details(
    notes: list[NoteItem],
    keywords: list[str] | None = None,
    concurrency: int = DETAIL_ENRICH_CONCURRENCY,
    total_timeout: int = DETAIL_ENRICH_TOTAL_SECONDS,
) -> list[NoteItem]:
    request = SearchCrawlRequest(
        keywords=keywords or [next((note.keyword_used for note in notes if note.keyword_used), "小红书")],
        target_count=max(1, len(notes)),
        detail_mode="none",
    )
    crawler = XHSCrawler(request)
    return await crawler.enrich_note_details(
        notes,
        concurrency=concurrency,
        total_timeout=total_timeout,
    )
