"""Мобильная раскладка в настоящем браузере (Playwright, Chromium, экран телефона 390×844, касания).

    .venv/bin/python scripts/dev_server.py &      # поднять стек
    .venv/bin/python tests/e2e/e2e_mobile.py [--shots DIR] [--channel chrome]

Проверяет: вход и восстановление ключей, чаты и группы, поиск, пересылку и файлы,
настройки, смену PIN и блокировку после 10 ошибок с часовым поясом телефона.
Проверяет раскладку 390×844 и 320×700: навигацию, меню по долгому нажатию,
окна и отсутствие горизонтальной прокрутки.
"""
from __future__ import annotations

import argparse
import base64
import re
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


def assert_composer_controls_aligned(page: Page, where: str) -> None:
    """Центр кнопки вложения совпадает с центром нижней строки поля ввода."""
    attach = page.locator(".comp-attach").bounding_box()
    input_row = page.locator(".comp-input-row").bounding_box()
    assert attach and input_row, f"{where}: composer controls are not visible"
    attach_center = attach["y"] + attach["height"] / 2
    input_center = input_row["y"] + input_row["height"] / 2
    assert abs(attach_center - input_center) <= 0.5, (
        f"{where}: attachment button center {attach_center} != input row center {input_center}"
    )


def register(page: Page, name: str, pin: str, errors: list[str], shot=None) -> None:
    page.on("console", lambda m: errors.append(f"{name}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
    page.on("response", lambda r: errors.append(f"{name}: HTTP {r.status} {r.url}") if r.status >= 400 else None)
    page.goto(BASE + "/login?dev=new")
    expect(page.locator(".login-account-note")).to_contain_text("same wallet on any device")
    if shot:
        no_horizontal_scroll(page, "login")
        shot(page, "01-login")
    page.get_by_role("button", name="Connect TON Wallet").click()
    page.wait_for_url("**/app", timeout=15000)
    expect(page.locator(".passcode-title")).to_have_text("Create Your Profile", timeout=15000)
    expect(page.locator(".setup-profile-form .pfield")).to_have_count(2)
    expect(page.get_by_role("button", name="Switch wallet")).to_be_visible()
    if shot:
        no_horizontal_scroll(page, "profile setup")
        shot(page, "02-profile-setup")
    username = name.lower().replace(" ", "_")
    page.locator('[data-key="setupName"]').fill(name)
    page.locator('[data-key="setupUsername"]').fill(username)
    expect(page.locator('[data-key="setupName"]')).to_have_value(name)
    expect(page.locator('[data-key="setupUsername"]')).to_have_value(username)
    expect(page.get_by_role("button", name="Continue")).to_be_enabled()
    page.get_by_role("button", name="Continue").click()
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
    expect(page.locator(".set-row--account")).to_contain_text(name, timeout=5000)
    expect(page.locator(".set-row--account")).to_contain_text("@" + username, timeout=5000)
    page.locator(".icon-back").first.click()
    expect(page.locator(".sb-head")).to_be_visible()
    log(f"{name}: registered on a phone screen")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--channel", default=None, help="Installed browser channel, e.g. chrome")
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
        browser = p.chromium.launch(channel=args.channel)
        alice, bob = (browser.new_context(**PHONE).new_page() for _ in range(2))
        carol = browser.new_context(**PHONE, timezone_id="Pacific/Honolulu").new_page()

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
        assert_composer_controls_aligned(alice, "390px chat")
        alice.locator("#dc-draft").fill("Hello **Bob**! A long line to check wrapping on a narrow phone screen: "
                                         "https://example.com/some/really/long/path/that/should/not/overflow")
        alice.keyboard.press("Enter")
        expect(alice.locator(".bubble.is-mine").last).to_contain_text("Hello", timeout=10000)
        no_horizontal_scroll(alice, "alice chat")
        shot(alice, "05-chat")
        log("alice: chat opened full screen, message sent")

        # --- Боб: список → чат → видимая кнопка «⋯» → «Reply» ---
        bob_thread = thread(bob, f"Alice {RUN}")
        expect(bob_thread.locator(".thread__badge")).to_have_text("1", timeout=15000)
        shot(bob, "06-list")
        bob_thread.click()
        expect(bob.locator(".sidebar")).to_be_hidden()
        incoming = bob.locator(".msg-row").filter(has_text="Hello").last
        expect(incoming).to_be_visible(timeout=10000)
        actions = incoming.locator(".msg-actions")
        dots = actions.locator(".msg-icon").first
        expect(actions).to_be_visible()
        expect(dots).to_be_visible()
        expect(actions.locator(".msg-icon").nth(1)).to_be_hidden()
        bubble_box = incoming.locator(".bubble").bounding_box()
        dots_box = dots.bounding_box()
        assert bubble_box and dots_box
        assert 0 <= dots_box["x"] - (bubble_box["x"] + bubble_box["width"]) <= 3, (
            f"message dots are not next to the bubble: bubble={bubble_box}, dots={dots_box}"
        )
        right_gutter = bob.viewport_size["width"] - bubble_box["x"] - bubble_box["width"]
        assert right_gutter <= 37, f"long received message has a large right gutter: {right_gutter}px"
        dots.click()
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
        log("bob: visible message dots opened the menu, reply sent")

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
        action_boxes = alice.locator(".paction").evaluate_all(
            "els => els.map(el => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right}; })"
        )
        action_gaps = [action_boxes[i + 1]["left"] - action_boxes[i]["right"] for i in range(3)]
        assert min(action_gaps) >= 18, f"profile actions are too close together: {action_gaps}"
        assert alice.locator(".paction__label").first.evaluate(
            "el => getComputedStyle(el).userSelect"
        ) == "none", "profile action labels can still be selected/copied"
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

        # Замена аватарки на телефоне сразу обновляет её в сообщениях у участников группы.
        alice.locator(".chat-head__back").click()
        alice.locator(".sb-pill").click()
        alice.locator(".filter-row", has_text="Settings").click()
        alice.locator(".set-row--account").click()
        avatar_png = {
            "name": "alice-mobile.png",
            "mimeType": "image/png",
            "buffer": base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            ),
        }
        alice.locator("#dc-avatar-input").set_input_files(avatar_png)
        expect(alice.locator(".toast")).to_contain_text("Photo updated", timeout=15000)
        expect(alice.locator(".pedit-avatar")).to_have_attribute(
            "style", re.compile(r"/api/media/avatars/"), timeout=15000
        )
        expect(row.locator(".msg-row__av")).to_have_attribute(
            "style", re.compile(r"/api/media/avatars/"), timeout=15000
        )
        alice.locator(".sheet--profile .pback").click()
        alice.locator(".icon-back").first.click()
        thread(alice, f"Crew {RUN}").click()
        log("alice and carol: avatar upload and live group-message refresh work on phones")

        # --- настройки на весь экран ---
        carol.locator(".chat-head__back").click()
        carol.locator(".sb-pill").click()
        carol.locator(".filter-row", has_text="Settings").click()
        expect(carol.locator(".screen-title")).to_have_text("Settings")
        expect(carol.locator(".set-note--account-access")).to_contain_text("account does not expire")
        expect(carol.locator(".set-note--account-access")).to_contain_text("same TON wallet")
        no_horizontal_scroll(carol, "settings")
        shot(carol, "12-settings")
        carol.locator(".set-danger", has_text="Delete all media").click()
        dlg = carol.locator(".sheet--dialog")
        expect(dlg).to_be_visible()
        shot(carol, "13-dialog")
        carol.locator(".btn-cancel").click()
        log("carol: settings and dialog fit the screen")

        # --- настройки: навигация, переключатели и PIN на телефоне ---
        carol.locator('[data-click="goAutolock"]').click()
        expect(carol.locator(".screen-title")).to_have_text("Lock after inactivity")
        carol.locator('[data-click="pickAutolock"][data-val="5 minutes"]').click()
        expect(carol.locator('[data-click="goAutolock"]')).to_contain_text("5 minutes")
        carol.locator('[data-click="toggleOnline"]').click()
        expect(carol.locator('[data-click="toggleOnline"]')).not_to_have_class("track is-on")
        carol.locator('[data-click="toggleOnline"]').click()
        carol.locator('[data-click="toggleReceipts"]').click()
        expect(carol.locator('[data-click="toggleReceipts"]')).not_to_have_class("track is-on")
        carol.locator('[data-click="toggleReceipts"]').click()
        carol.locator('[data-click="goGroupPerm"]').click()
        carol.locator('[data-click="pickGroupPerm"][data-val="No one"]').click()
        carol.locator('[data-click="closeGroupPerm"]').click()
        expect(carol.locator('[data-click="goGroupPerm"]')).to_contain_text("No one")
        carol.locator('[data-click="goGroupPerm"]').click()
        carol.locator('[data-click="pickGroupPerm"][data-val="Everyone"]').click()
        carol.locator('[data-click="closeGroupPerm"]').click()
        carol.locator('[data-click="pickRetention"][data-val="Keep all"]').click()
        expect(carol.locator('[data-click="pickRetention"][data-val="Keep all"] .radio')).to_have_class("radio is-on")
        carol.locator('[data-click="pickRetention"][data-val="30 days"]').click()
        carol.locator('[data-click="goSessions"]').click()
        expect(carol.locator(".screen-title")).to_have_text("Active sessions")
        expect(carol.locator(".pill-current")).to_have_count(1)
        no_horizontal_scroll(carol, "sessions")
        carol.locator('[data-click="closeSessions"]').click()

        carol.locator('[data-click="goPasscode"]').click()
        expect(carol.locator(".passcode-title")).to_have_text("Change Passcode")
        carol.locator("#dc-passcode-input").fill("4321")
        expect(carol.locator(".passcode-title")).to_have_text("New Passcode", timeout=10000)
        carol.locator("#dc-passcode-input").fill("9876")
        expect(carol.locator(".passcode-title")).to_have_text("Confirm Passcode")
        carol.locator("#dc-passcode-input").fill("9876")
        expect(carol.locator(".screen-title")).to_have_text("Settings", timeout=30000)
        assert carol.evaluate("location.pathname") == "/app/settings"
        carol.locator('[data-click="lockNow"]').click()
        expect(carol.locator(".passcode-title")).to_have_text("Enter Passcode")
        type_pin(carol, "4321")
        expect(carol.locator(".gate-error")).to_contain_text("Wrong passcode", timeout=10000)
        type_pin(carol, "9876")
        expect(carol.locator(".screen-title")).to_have_text("Settings", timeout=20000)
        log("carol: settings, active sessions, PIN change and mobile unlock work")

        # --- поиск, пересылка, участники и исчезающие сообщения ---
        alice.locator(".chat-head__peer").click()
        alice.locator('[data-click="toggleMore"]').click()
        alice.locator('[data-click="openMembers"]').click()
        expect(alice.locator(".members-count")).to_have_text("3 people", timeout=10000)
        no_horizontal_scroll(alice, "group members")
        alice.locator(".sheet--members .sheet-x").click()
        alice.locator(".chat-head__peer").click()
        alice.locator('[data-click="goDisappearing"]').click()
        alice.locator('[data-click="pickVanish"][data-val="5 minutes"]').click()
        expect(alice.locator('[data-click="pickVanish"][data-val="5 minutes"]')).to_contain_text("5 minutes")
        alice.locator(".sheet--profile .pback").click()
        expect(alice.locator('[data-click="goDisappearing"]')).to_contain_text("5 minutes")
        alice.locator(".sheet--profile .pback").click()

        alice.locator(".chat-head__peer").click()
        alice.locator('[data-click="searchFromProfile"]').click()
        alice.locator(".msg-search-box input").fill("Welcome")
        expect(alice.locator(".msg-result")).to_have_count(1, timeout=10000)
        no_horizontal_scroll(alice, "message search")
        alice.locator(".msg-search-close").click()

        long_press(alice, alice.locator(".bubble.is-mine").filter(has_text="Welcome"))
        alice.locator(".msg-menu__item", has_text="Forward").click()
        expect(alice.locator(".sheet--forward")).to_be_visible()
        alice.locator(".fwd-search input").fill(f"Bob {RUN}")
        alice.locator(".fwd-row", has_text=f"Bob {RUN}").click()
        expect(alice.locator(".toast")).to_contain_text("Forwarded", timeout=15000)
        thread(bob, f"Alice {RUN}").click()
        expect(bob.locator(".bubble").filter(has_text="Welcome to the group")).to_be_visible(timeout=15000)
        log("alice: mobile group members, disappearing messages, search and forwarding work")

        # --- вложение: выбор, удаление из черновика, отправка и скачивание ---
        alice.locator(".chat-head__back").click()
        thread(alice, f"Bob {RUN}").click()
        mobile_file = {"name": "mobile-check.txt", "mimeType": "text/plain", "buffer": b"encrypted mobile attachment"}
        alice.locator("#dc-file-input").set_input_files(mobile_file)
        expect(alice.locator(".attach__name")).to_contain_text("mobile-check.txt")
        no_horizontal_scroll(alice, "attachment draft")
        alice.locator(".attach__x").click()
        expect(alice.locator(".attach-strip")).to_have_count(0)
        alice.locator("#dc-file-input").set_input_files(mobile_file)
        alice.locator(".comp-send").click()
        expect(bob.locator(".file-card__name")).to_contain_text("mobile-check.txt", timeout=20000)
        file_row = bob.locator(".msg-row").filter(has_text="mobile-check.txt").last
        file_row.locator(".msg-actions .msg-icon").first.click()
        expect(bob.locator(".msg-menu__item", has_text="Download")).to_be_visible()
        with bob.expect_download(timeout=20000) as download_info:
            bob.locator(".msg-menu__item", has_text="Download").click()
        download = download_info.value
        assert download.suggested_filename == "mobile-check.txt"
        assert Path(download.path()).read_bytes() == mobile_file["buffer"]
        log("alice and bob: attachment sent, decrypted and downloaded on phones")

        # --- блоклист и отображение времени блокировки в часовом поясе телефона ---
        carol.locator('[data-click="goBlacklist"]').click()
        carol.locator(".pill-search input").fill(f"Bob {RUN}")
        expect(carol.locator(".person-row", has_text=f"Bob {RUN}")).to_be_visible(timeout=10000)
        carol.locator(".person-row", has_text=f"Bob {RUN}").locator(".btn-block").click()
        expect(carol.locator(".person-row", has_text=f"Bob {RUN}").locator(".btn-unblock")).to_be_visible(timeout=10000)
        carol.locator(".person-row", has_text=f"Bob {RUN}").locator(".btn-unblock").click()
        expect(carol.locator(".btn-unblock")).to_have_count(0, timeout=10000)
        carol.locator('[data-click="closeBlacklist"]').click()
        carol.locator('[data-click="lockNow"]').click()
        for attempt in range(1, 11):
            type_pin(carol, "0000")
            if attempt < 10:
                expect(carol.locator(".gate-error")).to_contain_text(f"{10 - attempt} attempts left", timeout=10000)
        expect(carol.locator(".gate-error")).to_contain_text("Try again at", timeout=10000)
        expect(carol.locator(".gate-error")).to_contain_text("HST")
        expect(carol.locator("#dc-gate-input")).to_be_disabled()
        no_horizontal_scroll(carol, "PIN lockout")
        log("carol: blocklist and 10-error PIN lockout use the phone time zone")

        # --- новый телефон: восстановление ключей и истории по PIN ---
        seed = alice.evaluate("localStorage.getItem('cx_dev_wallet_seed')")
        fresh_ctx = browser.new_context(**PHONE)
        fresh_ctx.add_init_script(f"localStorage.setItem('cx_dev_wallet_seed', {seed!r})")
        fresh = fresh_ctx.new_page()
        fresh.on("pageerror", lambda e: errors.append(f"fresh phone: {e}"))
        fresh.goto(BASE + "/login")
        fresh.get_by_role("button", name="Connect TON Wallet").click()
        expect(fresh.locator(".passcode-title")).to_have_text("Restore Account", timeout=15000)
        expect(fresh.locator(".passcode-text")).to_contain_text("encrypted chats on this device")
        expect(fresh.get_by_role("button", name="Switch wallet")).to_be_visible()
        no_horizontal_scroll(fresh, "mobile key restore")
        type_pin(fresh, "1234")
        expect(thread(fresh, f"Bob {RUN}")).to_be_visible(timeout=30000)
        thread(fresh, f"Bob {RUN}").click()
        expect(fresh.locator(".bubble").filter(has_text="got it")).to_be_visible(timeout=15000)
        fresh_ctx.close()
        log("fresh phone: 2-of-3 key recovery and decrypted chat history work")

        # Самая узкая поддерживаемая ширина: список, чат, профиль и настройки.
        alice.set_viewport_size({"width": 320, "height": 700})
        assert_composer_controls_aligned(alice, "320px chat")
        no_horizontal_scroll(alice, "320px chat")
        alice.locator(".chat-head__peer").click()
        no_horizontal_scroll(alice, "320px profile")
        alice.locator(".sheet--profile .pback").click()
        alice.locator(".chat-head__back").click()
        no_horizontal_scroll(alice, "320px list")
        alice.locator(".sb-pill").click()
        alice.locator(".filter-row", has_text="Settings").click()
        no_horizontal_scroll(alice, "320px settings")
        log("alice: chat, profile, list and settings fit a 320px phone")

        browser.close()

    if errors:
        print("console errors:\n  " + "\n  ".join(errors))
        return 1
    log("MOBILE E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
