#!/usr/bin/env python3
"""Permanently delete one CryptisDchat user from the live system.

Run from the project root on the Docker host, usually over SSH:
    python3 scripts/helpers/delete_user.py --username alice --name "Alice Smith"
    python3 scripts/helpers/delete_user.py --list-users

Old database dump files are intentionally not modified by this utility.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"


class DeleteUserError(RuntimeError):
    pass


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise DeleteUserError(f"Не найден файл конфигурации: {path}")
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise DeleteUserError(f"Неверная строка {line_number} в .env")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise DeleteUserError(f"Неверное имя переменной в .env, строка {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def run_compose(args: list[str], *, env: dict[str, str], input_text: str | None = None,
                timeout: int = 30) -> str:
    result = subprocess.run(
        ["docker", "compose", *args], cwd=ROOT, env=env, input=input_text,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
        raise DeleteUserError(detail)
    return result.stdout.strip()


def db_cli(env_values: dict[str, str], sql: str) -> str:
    db_user = env_values.get("DB_USER", "cryptis_user")
    db_name = env_values.get("DB_NAME", "cryptisdchat")
    password = env_values.get("DB_PASSWORD", "")
    if not password:
        raise DeleteUserError("В .env не задан DB_PASSWORD")
    command_env = os.environ.copy()
    command_env["MYSQL_PWD"] = password
    return run_compose(
        ["exec", "-T", "-e", "MYSQL_PWD", "db", "mariadb", "--batch", "--raw",
         "--skip-column-names", "--default-character-set=utf8mb4", "-u", db_user,
         db_name, "-e", sql], env=command_env,
    )


def lookup_user(env_values: dict[str, str], username: str, display_name: str) -> tuple[str, str, str] | None:
    username_hex = username.encode("utf-8").hex()
    name_hex = display_name.encode("utf-8").hex()
    rows = db_cli(
        env_values,
        "SELECT id, HEX(username), HEX(display_name) FROM users "
        f"WHERE BINARY username = X'{username_hex}' "
        f"AND BINARY display_name = X'{name_hex}' LIMIT 2",
    )
    matches = [line.split("\t") for line in rows.splitlines() if line.strip()]
    if len(matches) > 1:
        raise DeleteUserError("Найдено несколько совпадений; удаление отменено")
    if not matches:
        return None
    try:
        user_id, user_hex, name_hex_result = matches[0]
        return (user_id, bytes.fromhex(user_hex).decode("utf-8"),
                bytes.fromhex(name_hex_result).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise DeleteUserError("Не удалось разобрать результат поиска пользователя") from exc


def list_users(env_values: dict[str, str]) -> int:
    rows = db_cli(
        env_values,
        "SELECT id, COALESCE(HEX(username), ''), HEX(display_name) "
        "FROM users ORDER BY username, display_name, id",
    )
    users: list[tuple[str, str, str]] = []
    for line in rows.splitlines():
        if not line.strip():
            continue
        try:
            user_id, username_hex, name_hex = line.split("\t")
            username = bytes.fromhex(username_hex).decode("utf-8") if username_hex else ""
            display_name = bytes.fromhex(name_hex).decode("utf-8") if name_hex else ""
            users.append((user_id, username, display_name))
        except (ValueError, UnicodeDecodeError) as exc:
            raise DeleteUserError("Не удалось разобрать список пользователей") from exc

    if not users:
        print("Пользователей нет.")
        return 0
    id_width = max(len("ID"), *(len(user[0]) for user in users))
    username_width = max(len("USERNAME"), *(len(user[1]) for user in users))
    print(f"{'ID':<{id_width}}  {'USERNAME':<{username_width}}  ИМЯ")
    for user_id, username, display_name in users:
        print(f"{user_id:<{id_width}}  {username:<{username_width}}  {display_name}")
    print(f"Всего: {len(users)}")
    return 0


def run_cleanup_task(task_name: str, user_id: str, *, service: str) -> str:
    runner = (
        "import sys; from worker.celery_app import celery_app; "
        "r=celery_app.send_task(sys.argv[1], args=[sys.argv[2]]); "
        "print(r.get(timeout=600, propagate=True))"
    )
    return run_compose(
        ["exec", "-T", service, "python", "-c", runner, task_name, user_id],
        env=os.environ.copy(), timeout=660,
    )


def delete_database_user(user_id: str) -> str:
    cleanup = r'''
import os, sys, pymysql
uid = sys.argv[1]
conn = pymysql.connect(
    host=os.environ["DB_HOST"], port=int(os.environ.get("DB_PORT", "3306")),
    user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
    database=os.environ["DB_NAME"], charset="utf8mb4", autocommit=False,
)
counts = {}
try:
    with conn.cursor() as c:
        c.execute("UPDATE threads t JOIN (SELECT thread_id, MIN(user_id) replacement_id "
                  "FROM thread_members WHERE user_id <> %s GROUP BY thread_id) m "
                  "ON m.thread_id=t.id SET t.created_by=m.replacement_id WHERE t.created_by=%s",
                  (uid, uid))
        counts["threads transferred"] = c.rowcount
        c.execute("DELETE FROM threads WHERE created_by=%s", (uid,))
        counts["owner-only threads removed"] = c.rowcount
        c.execute("UPDATE threads SET avatar_upload_id=NULL WHERE avatar_upload_id IN "
                  "(SELECT id FROM attachments WHERE owner_id=%s)", (uid,))
        c.execute("UPDATE group_tree_nodes SET owner_user_id=NULL WHERE owner_user_id=%s", (uid,))
        c.execute("UPDATE group_membership_events SET subject_user_id=NULL WHERE subject_user_id=%s", (uid,))
        c.execute("DELETE FROM reports WHERE reporter_id=%s OR target_user_id=%s", (uid, uid))
        c.execute("DELETE FROM chain_events WHERE actor_id=%s OR message_id IN "
                  "(SELECT id FROM messages WHERE sender_id=%s)", (uid, uid))
        c.execute("DELETE FROM group_membership_events WHERE actor_id=%s", (uid,))
        c.execute("DELETE FROM group_key_commits WHERE committer_id=%s", (uid,))
        c.execute("DELETE FROM messages WHERE sender_id=%s", (uid,))
        counts["messages removed"] = c.rowcount
        c.execute("DELETE FROM users WHERE id=%s", (uid,))
        if c.rowcount != 1:
            raise RuntimeError("user row changed or disappeared; transaction cancelled")
    conn.commit()
    print("; ".join(f"{k}: {v}" for k, v in counts.items()))
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()
'''
    return run_compose(
        ["exec", "-T", "backend", "python", "-c", cleanup, user_id],
        env=os.environ.copy(), timeout=120,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Безвозвратно удалить пользователя CryptisDchat и его текущие данные."
    )
    parser.add_argument("--list-users", action="store_true", help="Показать всех пользователей")
    parser.add_argument("--username", help="Точное имя пользователя")
    parser.add_argument("--name", help="Точное отображаемое имя профиля")
    parser.add_argument("--yes", action="store_true", help="Подтвердить удаление без интерактивного запроса")
    parser.add_argument("--dry-run", action="store_true", help="Только найти и показать аккаунт")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cleanup_started = False
    try:
        env_values = read_env_file(ENV_FILE)
        if args.list_users:
            if args.username or args.name or args.yes or args.dry_run:
                raise DeleteUserError("--list-users нельзя совмещать с параметрами удаления")
            return list_users(env_values)
        if not args.username or not args.name:
            raise DeleteUserError("Для удаления укажите --username и --name; для списка используйте --list-users")
        user = lookup_user(env_values, args.username, args.name)
        if user is None:
            print("Пользователь с точным совпадением username и имени не найден.", file=sys.stderr)
            return 2
        user_id, username, display_name = user
        print(f"Найден аккаунт: @{username} — {display_name} (id {user_id})")
        if args.dry_run:
            print("Проверка завершена; данные не изменялись.")
            return 0
        if not args.yes:
            print("Будут удалены профиль, сообщения, вложения, ключи и текущие данные аккаунта.")
            print("Старые резервные дампы общей базы эта команда не меняет.")
            confirmation = input(f"Для безвозвратного удаления введите @{username}: ").strip()
            if confirmation != f"@{username}":
                print("Подтверждение не совпало; удаление отменено.", file=sys.stderr)
                return 3

        cleanup_started = True
        print("Удаляю резервные ключи из Realm-сервисов...")
        run_cleanup_task("keys.destroy_all", user_id, service="worker")
        print("Удаляю файлы пользователя...")
        run_cleanup_task("media.delete_all_for_user", user_id, service="worker_fast")
        print("Удаляю данные пользователя из базы...")
        summary = delete_database_user(user_id)
        print("Пользователь и его текущие данные удалены.")
        if summary:
            print(summary)
        return 0
    except (DeleteUserError, subprocess.TimeoutExpired, OSError, EOFError) as exc:
        partial = "; предыдущие внешние этапы могли уже удалить часть данных" if cleanup_started else ""
        print(f"Удаление не завершено{partial}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
