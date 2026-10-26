"""Админ-панель в настоящем браузере: desktop и узкий телефонный экран.

    .venv/bin/python scripts/dev_server.py
    .venv/bin/python tests/e2e/e2e_admin.py [--shots DIR] [--channel chrome]

Проверяет вход, основные разделы, активное верхнее меню, focus-visible,
локальную прокрутку широких таблиц и отсутствие горизонтального скролла страницы.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ADMIN = "http://127.0.0.1:3891/admin"
APP = "http://localhost:3890"


def log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def no_page_overflow(page: Page, where: str) -> None:
    dimensions = page.evaluate("""() => ({
        page: document.documentElement.scrollWidth,
        viewport: document.documentElement.clientWidth,
    })""")
    assert dimensions["page"] <= dimensions["viewport"] + 1, f"{where}: page overflow {dimensions}"


def assert_section(page: Page, title: str, active: str, shot=None) -> None:
    expect(page.locator("h1")).to_contain_text(title)
    expect(page.locator('.admin-nav__link[aria-current="page"]')).to_have_text(active)
    expect(page.locator('link[href*="/admin/static/css/admin.css"]')).to_have_count(1)
    no_page_overflow(page, title)
    if shot:
        shot(page, title.lower().replace(" ", "-"))


def ensure_user(browser) -> None:
    """Создаёт узнаваемую сервером учётную запись, если dev-база была пустой."""
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    page.goto(APP + "/login?dev=new")
    page.get_by_role("button", name="Connect TON Wallet").click()
    page.wait_for_url("**/app", timeout=15000)
    context.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shots", default=None)
    parser.add_argument("--channel", default=None, help="Installed browser channel, e.g. chrome")
    args = parser.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=args.channel)
        ensure_user(browser)
        context = browser.new_context(viewport={"width": 1280, "height": 820})
        page = context.new_page()
        page.on("console", lambda message: errors.append(f"console: {message.text}") if message.type == "error" else None)
        page.on("pageerror", lambda error: errors.append(f"page: {error}"))
        page.on("response", lambda response: errors.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400 else None)

        def shot(current: Page, name: str) -> None:
            if shots:
                current.screenshot(path=str(shots / f"admin-{name}.png"), full_page=True)

        page.goto(ADMIN + "/login")
        no_page_overflow(page, "login")
        page.get_by_label("Username").fill("admin")
        page.locator("#password").fill("devpassword123")
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/admin/", timeout=10000)

        assert_section(page, "Dashboard", "Dashboard", shot)
        page.locator("body").click(position={"x": 4, "y": 200})
        page.keyboard.press("Tab")
        focus = page.evaluate("""() => ({
            tag: document.activeElement && document.activeElement.tagName,
            shadow: document.activeElement ? getComputedStyle(document.activeElement).boxShadow : 'none',
        })""")
        assert focus["tag"] == "A" and focus["shadow"] != "none", f"focus indicator missing: {focus}"

        page.get_by_role("link", name="Users", exact=True).click()
        assert_section(page, "Users", "Users", shot)
        expect(page.locator(".user-row")).not_to_have_count(0)
        page.locator(".user-row .person").first.click()
        expect(page.locator('.admin-nav__link[aria-current="page"]')).to_have_text("Users")
        no_page_overflow(page, "user detail")
        shot(page, "user-detail")

        page.get_by_role("link", name="Reports", exact=True).click()
        assert_section(page, "Reports", "Reports", shot)
        page.get_by_role("link", name="Blockchain queue", exact=True).click()
        assert_section(page, "TON write queue", "Blockchain queue", shot)
        log("desktop: login, navigation, detail page and focus indicator work")

        page.set_viewport_size({"width": 320, "height": 700})
        page.get_by_role("link", name="Users", exact=True).click()
        assert_section(page, "Users", "Users", shot)
        nav = page.locator(".admin-nav")
        nav_size = nav.evaluate("el => ({scroll: el.scrollWidth, client: el.clientWidth})")
        assert nav_size["scroll"] > nav_size["client"], f"mobile nav is not locally scrollable: {nav_size}"
        table = page.locator(".table-wrap").first
        table_size = table.evaluate("el => ({scroll: el.scrollWidth, client: el.clientWidth})")
        assert table_size["scroll"] > table_size["client"], f"mobile table is not locally scrollable: {table_size}"
        no_page_overflow(page, "320px users")

        page.get_by_role("link", name="Blockchain queue", exact=True).click()
        assert_section(page, "TON write queue", "Blockchain queue", shot)
        no_page_overflow(page, "320px queue")
        log("mobile: navigation and tables scroll locally without widening the page")

        context.close()
        browser.close()

    if errors:
        print("browser errors:\n  " + "\n  ".join(errors))
        return 1
    log("ADMIN E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
