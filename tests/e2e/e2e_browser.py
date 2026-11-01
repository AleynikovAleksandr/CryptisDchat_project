"""Сквозной сценарий в настоящем браузере (Playwright, Chromium) против scripts/helpers/dev_server.py.

    .venv/bin/python scripts/helpers/dev_server.py &      # поднять стек
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


def check_device_info(browser) -> None:
    """Exercise the shared classifier and real session renderer without a dev server."""
    root = Path(__file__).resolve().parents[2]
    page = browser.new_page()
    page.set_content('<main id="sessions"></main>')
    page.add_script_tag(path=str(root / "backend/static/js/lib/device-info.js"))
    cases = [
        ("Mozilla/5.0 (Linux; Android 14) Chrome/124.0 Mobile Safari/537.36", 0, "Android phone · Chrome"),
        ("Mozilla/5.0 (Linux; Android 14) Chrome/124.0 Safari/537.36", 0, "Android tablet · Chrome"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Version/17.4 Safari/604.1", 5, "iOS phone · Safari"),
        ("Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) Version/17.4 Safari/604.1", 5, "iPadOS tablet · Safari"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) Version/17.4 Safari/605.1", 5, "iPadOS tablet · Safari"),
        ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36 Edg/124.0", 0, "Windows computer · Edge"),
        ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) Version/17.4 Safari/605.1", 0, "macOS computer · Safari"),
        ("Mozilla/5.0 (X11; Linux x86_64) Firefox/125.0", 0, "Linux computer · Firefox"),
        ("Unrecognized client", 0, "Web browser"),
    ]
    labels = []
    for ua, touch_points, expected in cases:
        info = page.evaluate("([ua, touch]) => CX.deviceInfo.detect(ua, touch)", [ua, touch_points])
        assert info["label"] == expected, info
        labels.append(expected)
    page.add_script_tag(content="""
        const state = { sessions: [] };
        const I = { back: '' };
        function esc(value) {
            const el = document.createElement('span');
            el.textContent = value == null ? '' : String(value);
            return el.innerHTML;
        }
        function relTime() { return 'just now'; }
    """)
    page.add_script_tag(path=str(root / "backend/static/js/app/views.js"))
    names = labels + ["Chrome on macOS", "Legacy <device>", ""]
    page.evaluate("""names => {
        state.sessions = names.map((name, index) => ({
            device_id: String(index), name, current: index === 0,
            user_agent: '', ip_address: '127.0.0.1',
            created_at: '2026-11-01T00:00:00Z', last_seen_at: '2026-11-01T00:00:00Z'
        }));
        document.getElementById('sessions').innerHTML = sessionsHtml();
    }""", names)
    rows = page.locator('.person-row__name')
    assert rows.all_text_contents() == [names[0] + " This device"] + names[1:-1] + ["Web browser"]
    expect(page.locator('.pill-current')).to_have_count(1)
    expect(page.locator('device')).to_have_count(0)
    page.close()
    log("device info: 9 platform cases and stored/legacy session labels rendered")


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
    expect(page.locator(".passcode-title")).to_have_text("Create Your Profile", timeout=15000)
    page.locator('[data-key="setupName"]').fill(name)
    page.locator('[data-key="setupUsername"]').fill(name.lower().replace(" ", "_"))
    page.get_by_role("button", name="Continue").click()
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
    ap.add_argument("--channel", default=None, help="Installed browser channel, e.g. chrome")
    ap.add_argument("--device-info-only", action="store_true", help="Check platform labels and sessions without a dev server")
    ap.add_argument("--pairing-only", action="store_true", help="Offline pairing UI and WebCrypto integration")
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
        browser = p.chromium.launch(headless=not args.headed, channel=args.channel)
        if args.pairing_only:
            from pairing_checks import check_pairing
            check_pairing(browser, mobile=False)
            browser.close()
            return 0
        check_device_info(browser)
        if args.device_info_only:
            browser.close()
            return 0
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
        rapid_texts = [f"Fast message {i} {RUN}" for i in range(1, 5)]
        for message in rapid_texts:
            alice.locator("#dc-draft").fill(message)
            alice.keyboard.press("Enter")
        expect(alice.locator(".bubble.is-mine .bubble__text").last).to_have_text(rapid_texts[-1], timeout=15000)
        alice_order = alice.locator(".bubble__text").all_text_contents()
        assert [alice_order.index(message) for message in rapid_texts] == sorted(
            alice_order.index(message) for message in rapid_texts
        ), f"Alice's rapid sends were reordered: {alice_order}"
        log("alice: message sent (encrypted + signed, protobuf)")

        # Боб получает чат по WebSocket и открывает его
        bob_thread = thread(bob, f"Alice {RUN}")
        expect(bob_thread).to_be_visible(timeout=15000)
        expect(bob_thread.locator(".thread__badge")).to_have_text(str(1 + len(rapid_texts)), timeout=15000)
        bob_thread.click()
        bubble = bob.locator(".bubble").filter(has_text="Hello").last
        expect(bubble.locator("strong")).to_have_text("Bob", timeout=10000)
        expect(bubble.locator("li")).to_have_count(2)
        expect(bob.locator(".bubble__text").last).to_have_text(rapid_texts[-1], timeout=15000)
        bob_order = bob.locator(".bubble__text").all_text_contents()
        assert [bob_order.index(message) for message in rapid_texts] == sorted(
            bob_order.index(message) for message in rapid_texts
        ), f"Bob received rapid sends out of order: {bob_order}"
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
        row = carol.locator(".msg-row").filter(has_text="Welcome to the group")
        expect(row.locator(".msg-row__av")).to_have_text("A", timeout=10000)  # аватар автора (инициал Алисы)
        expect(alice.locator(".msg-row.is-mine .msg-row__av")).to_have_count(0)  # у своих сообщений аватара нет
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
        expect(laptop.locator(".passcode-title")).to_have_text("Restore Account", timeout=15000)
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
