"""Сквозной сценарий в настоящем браузере (Playwright, Chromium) против scripts/dev_server.py.

    .venv/bin/python scripts/dev_server.py &      # поднять стек
    .venv/bin/python tests/e2e/e2e_browser.py [--shots DIR]

Два изолированных браузерных профиля — Алиса и Боб:
вход dev-кошельком → создание PIN (генерация ключей, бэкап на 3 realm) → личный чат →
сообщение с Markdown → доставка по WebSocket → проверка подписи и расшифровка у получателя →
статус «прочитано» → подтверждение в блокчейне → группа с TreeKEM → блокировка и разблокировка PIN.
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

BASE = "http://localhost:3890"
RUN = time.strftime("%H%M%S")  # dev-база живёт между запусками — имена делаем уникальными


SHOT = None  # функция снимка экрана, задаётся в main()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def thread(page: Page, name: str):
    """Строка списка чатов по точному имени чата (превью может содержать имена других людей)."""
    return page.locator(".thread").filter(has=page.locator(".thread__name", has_text=re.compile("^" + re.escape(name) + "$")))


def type_pin(page: Page, pin: str) -> None:
    page.locator("#dc-gate-input").wait_for(state="attached", timeout=15000)
    page.keyboard.type(pin, delay=60)


def register(page: Page, name: str, pin: str, errors: list[str]) -> None:
    page.on("console", lambda m: errors.append(f"{name}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"{name}: {e}"))
    page.goto(BASE + "/login?dev=new")
    page.get_by_role("button", name="Connect TON Wallet").click()
    page.wait_for_url("**/app", timeout=15000)
    expect(page.locator(".passcode-title")).to_have_text("Create Passcode", timeout=15000)
    if name.startswith("Alice") and SHOT:
        SHOT(page, "02-create-passcode")
    type_pin(page, pin)
    expect(page.locator(".passcode-title")).to_have_text("Confirm Passcode", timeout=5000)
    type_pin(page, pin)
    expect(page.locator(".empty__title")).to_have_text("Start Conversation", timeout=30000)
    # имя профиля: Settings → Account → Edit profile
    page.locator(".sb-pill").click()
    page.locator(".filter-row", has_text="Settings").click()
    page.locator(".set-row--account").click()
    field = page.locator('input[data-key="editName"]')
    field.fill(name)
    page.locator(".pill-btn", has_text="Save").click()
    expect(page.locator(".set-row--account")).to_contain_text(name, timeout=5000)
    page.locator(".icon-back").first.click()
    log(f"{name}: registered, keys generated, profile saved")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=None)
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    def shot(page: Page, name: str) -> None:
        if shots:
            page.screenshot(path=str(shots / f"{name}.png"))

    global SHOT
    SHOT = shot

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        alice_ctx = browser.new_context(viewport={"width": 1280, "height": 820})
        bob_ctx = browser.new_context(viewport={"width": 1280, "height": 820})
        carol_ctx = browser.new_context(viewport={"width": 1280, "height": 820})
        alice, bob, carol = alice_ctx.new_page(), bob_ctx.new_page(), carol_ctx.new_page()

        alice.goto(BASE + "/login")
        shot(alice, "01-login")
        register(alice, f"Alice {RUN}", "1234", errors)
        shot(alice, "02b-empty")
        register(bob, f"Bob {RUN}", "5678", errors)
        register(carol, f"Carol {RUN}", "4321", errors)

        # --- личный чат: Алиса → Боб ---
        alice.locator(".sb-round[title='New message']").click()
        alice.locator(".new-search input").fill(f"Bob {RUN}")
        alice.locator(".dir-row", has_text=f"Bob {RUN}").click()
        expect(alice.locator(".chat-head__name")).to_have_text(f"Bob {RUN}", timeout=10000)
        alice.locator("#dc-draft").fill("Hello **Bob**! Here is `code` and a list:\n- one\n- two")
        alice.keyboard.press("Enter")
        expect(alice.locator(".bubble.is-mine").last).to_contain_text("Hello", timeout=10000)
        log("alice: message sent (encrypted + signed, protobuf)")

        # Боб получает чат по WebSocket и открывает его
        bob_thread = thread(bob, f"Alice {RUN}")
        expect(bob_thread).to_be_visible(timeout=15000)
        expect(bob_thread.locator(".thread__badge")).to_have_text("1", timeout=10000)
        bob_thread.click()
        bubble = bob.locator(".bubble").filter(has_text="Hello").last
        expect(bubble.locator("strong")).to_have_text("Bob", timeout=10000)
        expect(bubble.locator("li")).to_have_count(2)
        log("bob: message verified (ECDSA), decrypted, markdown rendered")
        shot(bob, "03-bob-chat")

        bob.locator("#dc-draft").fill("Hi Alice 👋 got it")
        bob.keyboard.press("Enter")
        expect(alice.locator(".bubble").filter(has_text="got it")).to_be_visible(timeout=15000)
        # Алиса видит «прочитано» у своего сообщения
        expect(alice.locator(".bubble.is-mine .msg-status[title='Read']").first).to_be_visible(timeout=15000)
        log("alice: reply received in real time, read receipt shown")

        # подтверждение в блокчейне (mock-сеть, батч по расписанию beat)
        expect(alice.locator(".bubble.is-mine .msg-chain").first).to_be_visible(timeout=60000)
        log("alice: message anchored in TON batch (mock network)")
        alice.locator(".bubble.is-mine").first.hover()
        alice.locator(".msg-actions.is-mine.is-visible .msg-icon").first.click()
        alice.locator(".msg-menu__item", has_text="Info").click()
        expect(alice.locator(".sheet--info")).to_contain_text("Merkle proof verified on this device", timeout=15000)
        shot(alice, "04-proof")
        alice.locator(".sheet--info .dialog-x").click()
        log("alice: Merkle proof verified locally against the batch root")

        # --- поиск по зашифрованной переписке (слепой индекс) ---
        alice.locator(".chat-head__peer").click()
        alice.locator(".paction", has_text="Search").click()
        alice.locator(".msg-search-box input").fill("got")
        expect(alice.locator(".msg-result")).to_have_count(1, timeout=10000)
        alice.locator(".msg-search-close").click()

        # --- группа: TreeKEM ---
        alice.locator(".sb-round[title='New message']").click()
        alice.locator(".create-group").click()
        alice.locator(".group-search input").fill(RUN)
        alice.locator(".dir-row", has_text=f"Bob {RUN}").click()
        alice.locator(".dir-row", has_text=f"Carol {RUN}").click()
        alice.locator(".btn-next").click()
        alice.locator(".group-name-box input").fill(f"Crew {RUN}")
        alice.locator(".btn-create").click()
        expect(alice.locator(".chat-head__name")).to_have_text(f"Crew {RUN}", timeout=10000)
        expect(alice.locator(".chat-head__sub")).to_have_text("3 members", timeout=60000)
        log("group: membership anchored in TON, key tree committed")
        alice.locator("#dc-draft").fill("Welcome to the group 🎉")
        alice.keyboard.press("Enter")
        expect(alice.locator(".bubble.is-mine").filter(has_text="Welcome")).to_be_visible(timeout=10000)
        thread(carol, f"Crew {RUN}").click()
        expect(carol.locator(".bubble").filter(has_text="Welcome to the group")).to_be_visible(timeout=20000)
        log("carol: group message decrypted with the TreeKEM epoch key")
        shot(carol, "05-group")

        # --- блокировка и разблокировка PIN (ключи только в зашифрованном OPFS) ---
        bob.reload()
        expect(bob.locator(".passcode-title")).to_have_text("Enter Passcode", timeout=15000)
        type_pin(bob, "0000")
        expect(bob.locator(".gate-error")).to_contain_text("Wrong passcode", timeout=10000)
        type_pin(bob, "5678")
        expect(thread(bob, f"Alice {RUN}")).to_be_visible(timeout=20000)
        log("bob: reload → PIN unlock of the encrypted local vault → auto-login by refresh token")
        shot(bob, "06-unlocked")

        # --- новое устройство (ТЗ 6.6): тот же кошелёк, чистый браузер → восстановление ключей по PIN ---
        seed = alice.evaluate("localStorage.getItem('cx_dev_wallet_seed')")
        laptop_ctx = browser.new_context(viewport={"width": 1280, "height": 820})
        laptop_ctx.add_init_script(f"localStorage.setItem('cx_dev_wallet_seed', {seed!r})")
        laptop = laptop_ctx.new_page()
        laptop.on("pageerror", lambda e: errors.append(f"laptop: {e}"))
        laptop.goto(BASE + "/login")
        laptop.get_by_role("button", name="Connect TON Wallet").click()
        expect(laptop.locator(".passcode-title")).to_have_text("Restore Your Keys", timeout=15000)
        shot(laptop, "07-restore")
        type_pin(laptop, "9999")
        expect(laptop.locator(".gate-error")).to_contain_text("attempts left", timeout=30000)
        type_pin(laptop, "1234")
        thread(laptop, f"Bob {RUN}").click(timeout=40000)
        expect(laptop.locator(".bubble").filter(has_text="got it")).to_be_visible(timeout=15000)
        log("new device: wrong PIN counted by realms, correct PIN → 2-of-3 shares → keys restored → history decrypted")

        # настройки
        laptop.locator(".sb-pill").click()
        laptop.locator(".filter-row", has_text="Settings").click()
        expect(laptop.locator(".screen-title")).to_have_text("Settings")
        shot(laptop, "08-settings")

        browser.close()

    real_errors = [e for e in errors if "favicon" not in e]
    if real_errors:
        log("console errors:\n  " + "\n  ".join(real_errors))
        return 1
    log("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
