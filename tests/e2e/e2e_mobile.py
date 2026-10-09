"""Мобильная раскладка в настоящем браузере (Playwright, Chromium, экран телефона 390×844, касания).

    .venv/bin/python scripts/dev_server.py &      # поднять стек
    .venv/bin/python tests/e2e/e2e_mobile.py [--shots DIR]

Проверяет: одна колонка (список ↔ чат) и кнопку «Назад», окна на весь экран и снизу,
меню сообщения по долгому нажатию, аватары в группе, настройки — и что ни один экран
не прокручивается по горизонтали.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from e2e_browser import BASE, RUN, log, thread, type_pin  # noqa: E402

PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "device_scale_factor": 2}


def no_horizontal_scroll(page: Page, where: str) -> None:
    """Ни страница, ни лента сообщений не шире экрана."""
    over = page.evaluate("""() => {
        const out = [];
        const w = document.documentElement.clientWidth;
        if (document.documentElement.scrollWidth > w) out.push('page ' + document.documentElement.scrollWidth + '>' + w);
        document.querySelectorAll('.chat-scroll, .sb-list, .screen-scroll, .pbody, .sheet-scroll').forEach((el) => {
            if (el.scrollWidth > el.clientWidth + 1) out.push(el.className + ' ' + el.scrollWidth + '>' + el.clientWidth);
        });
        return out;
    }""")
    assert not over, f"{where}: horizontal overflow {over}"


def long_press(page: Page, locator, ms: int = 650) -> None:
    """Настоящее касание через CDP: touchStart → пауза → touchEnd (как палец на экране)."""
    box = locator.bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    time.sleep(ms / 1000)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    cdp.detach()


def settled_box(locator, timeout: float = 3.0) -> dict:
    """Рамка элемента после окончания анимации появления (выезд снизу, сдвиг сбоку)."""
    end = time.time() + timeout
    prev = None
    while time.time() < end:
        box = locator.bounding_box()
        if box and box == prev:
            return box
        prev = box
        time.sleep(0.12)
    return prev


def register(page: Page, name: str, pin: str, errors: list[str], shot=None) -> None:
    page.on("console", lambda m: errors.append(f"{name}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
    page.goto(BASE + "/login?dev=new")
    if shot:
        no_horizontal_scroll(page, "login")
        shot(page, "01-login")
    page.get_by_role("button", name="Connect TON Wallet").click()
    page.wait_for_url("**/app", timeout=15000)
    expect(page.locator(".passcode-title")).to_have_text("Create Passcode", timeout=15000)
    if shot:
        no_horizontal_scroll(page, "pin")
        shot(page, "02-pin")
    type_pin(page, pin)
    expect(page.locator(".passcode-title")).to_have_text("Confirm Passcode", timeout=5000)
    type_pin(page, pin)
    # на телефоне после входа виден список чатов, а не пустой экран справа
    expect(page.locator(".sb-head")).to_be_visible(timeout=30000)
    expect(page.locator(".main")).to_be_hidden()
    page.locator(".sb-pill").click()
    page.locator(".filter-row", has_text="Settings").click()
    expect(page.locator(".sidebar")).to_be_hidden()
    page.locator(".set-row--account").click()
    page.locator('input[data-key="editName"]').fill(name)
    page.locator(".pill-btn", has_text="Save").click()
    expect(page.locator(".set-row--account")).to_contain_text(name, timeout=5000)
    page.locator(".icon-back").first.click()
    expect(page.locator(".sb-head")).to_be_visible()
    log(f"{name}: registered on a phone screen")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    args = ap.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str) -> None:
        if shots:
            page.wait_for_timeout(400)  # дождаться конца анимации въезда экрана/окна
            page.screenshot(path=str(shots / f"m{name}.png"))

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        alice, bob, carol = (browser.new_context(**PHONE).new_page() for _ in range(3))

        register(alice, f"Alice {RUN}", "1234", errors, shot)
        register(bob, f"Bob {RUN}", "5678", errors)
        register(carol, f"Carol {RUN}", "4321", errors)
        shot(alice, "03-list-empty")

        # --- новое сообщение: окно на весь экран → чат на весь экран ---
        alice.locator(".sb-round[title='New message']").click()
        sheet = alice.locator(".ov--new .sheet")
        expect(sheet).to_be_visible()
        sb = settled_box(sheet)
        assert sb["x"] == 0 and sb["width"] == 390, f"new-message sheet is not full screen: {sb}"
        shot(alice, "04-new-sheet")
        alice.locator(".new-search input").fill(f"Bob {RUN}")
        alice.locator(".dir-row", has_text=f"Bob {RUN}").click()
        expect(alice.locator(".chat-head__name")).to_have_text(f"Bob {RUN}", timeout=10000)
        expect(alice.locator(".sidebar")).to_be_hidden()
        expect(alice.locator(".chat-head__back")).to_be_visible()
        alice.locator("#dc-draft").fill("Hello **Bob**! A long line to check wrapping on a narrow phone screen: "
                                         "https://example.com/some/really/long/path/that/should/not/overflow")
        alice.keyboard.press("Enter")
        expect(alice.locator(".bubble.is-mine").last).to_contain_text("Hello", timeout=10000)
        no_horizontal_scroll(alice, "alice chat")
        shot(alice, "05-chat")
        log("alice: chat opened full screen, message sent")

        # --- Боб: список → чат → долгое нажатие → «Reply» ---
        bob_thread = thread(bob, f"Alice {RUN}")
        expect(bob_thread.locator(".thread__badge")).to_have_text("1", timeout=15000)
        shot(bob, "06-list")
        bob_thread.click()
        expect(bob.locator(".sidebar")).to_be_hidden()
        incoming = bob.locator(".msg-row").filter(has_text="Hello").last
        expect(incoming).to_be_visible(timeout=10000)
        expect(bob.locator(".msg-actions").first).to_be_hidden()  # без мыши кнопок наведения нет
        long_press(bob, incoming.locator(".bubble"))
        menu = bob.locator(".msg-menu")
        expect(menu).to_be_visible(timeout=3000)
        mb = menu.bounding_box()
        assert mb["x"] >= 0 and mb["x"] + mb["width"] <= 390, f"menu off screen: {mb}"
        shot(bob, "07-long-press-menu")
        bob.locator(".msg-menu__item", has_text="Reply").click()
        expect(bob.locator(".reply-draft")).to_be_visible()
        bob.locator("#dc-draft").fill("Hi Alice 👋 got it")
        bob.keyboard.press("Enter")
        expect(alice.locator(".bubble").filter(has_text="got it")).to_be_visible(timeout=15000)
        log("bob: long press opened the message menu, reply sent")

        # «Назад» возвращает к списку, и новые сообщения снова считаются непрочитанными
        bob.locator(".chat-head__back").click()
        expect(bob.locator(".sb-head")).to_be_visible()
        expect(bob.locator(".main")).to_be_hidden()
        alice.locator("#dc-draft").fill("one more")
        alice.keyboard.press("Enter")
        expect(thread(bob, f"Alice {RUN}").locator(".thread__badge")).to_have_text("1", timeout=15000)
        log("bob: back to the list, unread badge counts again")

        # --- профиль на весь экран, «Info» — окно снизу ---
        alice.locator(".chat-head__peer").click()
        prof = alice.locator(".sheet--profile")
        expect(prof).to_be_visible()
        pb = settled_box(prof)
        assert pb["x"] == 0 and pb["width"] == 390, f"profile is not full screen: {pb}"
        no_horizontal_scroll(alice, "profile")
        shot(alice, "08-profile")
        alice.locator(".sheet--profile .pback").first.click()
        expect(prof).to_be_hidden(timeout=3000)
        long_press(alice, alice.locator(".bubble.is-mine").first)
        alice.locator(".msg-menu__item", has_text="Info").click()
        info = alice.locator(".sheet--info")
        expect(info).to_be_visible()
        ib = settled_box(info)
        assert abs(ib["y"] + ib["height"] - 844) < 2 and ib["width"] == 390, f"info is not a bottom sheet: {ib}"
        shot(alice, "09-info-bottom-sheet")
        alice.locator(".sheet--info .dialog-x").click()
        log("alice: profile full screen, message info as a bottom sheet")

        # --- группа: аватары авторов на узком экране ---
        alice.locator(".chat-head__back").click()
        alice.locator(".sb-round[title='New message']").click()
        alice.locator(".create-group").click()
        alice.locator(".group-search input").fill(RUN)
        alice.locator(".dir-row", has_text=f"Bob {RUN}").click()
        alice.locator(".dir-row", has_text=f"Carol {RUN}").click()
        alice.locator(".btn-next").click()
        alice.locator(".group-name-box input").fill(f"Crew {RUN}")
        shot(alice, "10-group-name")
        alice.locator(".btn-create").click()
        expect(alice.locator(".chat-head__sub")).to_have_text("3 members", timeout=60000)
        alice.locator("#dc-draft").fill("Welcome to the group 🎉")
        alice.keyboard.press("Enter")
        thread(carol, f"Crew {RUN}").click()
        row = carol.locator(".msg-row").filter(has_text="Welcome to the group")
        expect(row.locator(".msg-row__av")).to_have_text("A", timeout=20000)
        no_horizontal_scroll(carol, "group")
        shot(carol, "11-group")
        log("carol: group chat with author avatars fits the screen")

        # --- настройки на весь экран ---
        carol.locator(".chat-head__back").click()
        carol.locator(".sb-pill").click()
        carol.locator(".filter-row", has_text="Settings").click()
        expect(carol.locator(".screen-title")).to_have_text("Settings")
        no_horizontal_scroll(carol, "settings")
        shot(carol, "12-settings")
        carol.locator(".set-danger", has_text="Delete all media").click()
        dlg = carol.locator(".sheet--dialog")
        expect(dlg).to_be_visible()
        shot(carol, "13-dialog")
        carol.locator(".btn-cancel").click()
        log("carol: settings and dialog fit the screen")

        browser.close()

    if errors:
        print("console errors:\n  " + "\n  ".join(errors))
        return 1
    log("MOBILE E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
