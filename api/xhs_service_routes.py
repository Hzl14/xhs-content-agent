from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path

from fastapi import APIRouter
from playwright.async_api import async_playwright

from legacy_app.services.local_site_crawler_service import NAV_TIMEOUT_MS, STATE_FILE, XHS_BASE


router = APIRouter(prefix="/xhs-service", tags=["XHS Service"])

LOGIN_TIMEOUT_SECONDS = 300
LOGIN_CHECK_INTERVAL_SECONDS = 2
LOGIN_MIN_VISIBLE_SECONDS = 45
LOGIN_STATE_MAX_AGE_SECONDS = 24 * 60 * 60

_login_task: asyncio.Task | None = None
_login_status: dict = {
    "state": "idle",
    "message": "",
    "updated_at": None,
}


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@router.get("/status")
def get_status() -> dict:
    state_path = Path(STATE_FILE)
    state_age_seconds = int(time.time() - state_path.stat().st_mtime) if state_path.exists() else None
    login_valid = state_age_seconds is not None and state_age_seconds <= LOGIN_STATE_MAX_AGE_SECONDS
    return {
        "running": _port_open("127.0.0.1", 18060),
        "port": 18060,
        "state_file": str(state_path),
        "state_file_exists": state_path.exists(),
        "state_age_seconds": state_age_seconds,
        "login_valid": login_valid,
        "login_available": True,
        "login_task": _login_status,
    }


@router.post("/login")
async def run_login() -> dict:
    global _login_task
    if _login_task and not _login_task.done():
        return {
            "success": True,
            "message": "小红书登录窗口已经打开，请先在那个窗口里完成登录，不要重复点击。",
            "login_task": _login_status,
        }

    _set_login_status("starting", "正在打开小红书登录窗口...")
    _login_task = asyncio.create_task(_refresh_login_state())
    return {
        "success": True,
        "message": "已打开小红书登录窗口，请在弹出的窗口里完成登录。登录窗口会保留一段时间，登录态会在后台自动保存。",
        "login_task": _login_status,
    }


async def _refresh_login_state() -> None:
    state_path = Path(STATE_FILE)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = await browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            await page.goto(XHS_BASE, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            _set_login_status("waiting", "请在打开的小红书窗口里完成登录。")

            started_at = time.monotonic()
            deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
            saved_state = False
            while time.monotonic() < deadline:
                elapsed = time.monotonic() - started_at
                has_session = await _has_xhs_session(context)
                logged_out = await _looks_logged_out(page)

                if has_session and not logged_out and not saved_state:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    await context.storage_state(path=str(state_path))
                    saved_state = True
                    _set_login_status(
                        "saved",
                        f"小红书登录完成，登录态已保存到 {STATE_FILE}，现在可以继续找素材。",
                    )
                elif has_session and not logged_out and elapsed < LOGIN_MIN_VISIBLE_SECONDS:
                    remaining = int(LOGIN_MIN_VISIBLE_SECONDS - elapsed)
                    _set_login_status(
                        "saved",
                        f"小红书登录完成，窗口将继续保留约 {remaining} 秒，登录态已在后台保存。",
                    )
                elif has_session and not logged_out:
                    await browser.close()
                    _set_login_status("saved", f"小红书登录完成，登录态已保存到 {STATE_FILE}，现在可以继续找素材。")
                    return
                else:
                    _set_login_status("waiting", "请在打开的小红书窗口里完成登录。")

                await page.wait_for_timeout(LOGIN_CHECK_INTERVAL_SECONDS * 1000)

            await browser.close()
            _set_login_status("timeout", "登录超时，请重新点击登录并在 300 秒内完成。")
    except Exception as exc:  # noqa: BLE001
        _set_login_status("failed", f"刷新小红书登录态失败：{exc}")


def _set_login_status(state: str, message: str) -> None:
    _login_status.clear()
    _login_status.update(
        {
            "state": state,
            "message": message,
            "updated_at": time.time(),
        }
    )


async def _has_xhs_session(context) -> bool:
    cookies = await context.cookies()
    for cookie in cookies:
        domain = str(cookie.get("domain") or "")
        name = str(cookie.get("name") or "")
        value = str(cookie.get("value") or "")
        if "xiaohongshu.com" in domain and name == "web_session" and value:
            return True
    return False


async def _looks_logged_out(page) -> bool:
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
        "登录小红书",
        "手机号登录",
    ]
    if any(text in body_text for text in logged_out_texts):
        return True

    selectors = [
        "input[type='tel']",
        "input[placeholder*='手机号']",
        "input[placeholder*='验证码']",
        ".login-container",
    ]
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0 and await loc.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False
