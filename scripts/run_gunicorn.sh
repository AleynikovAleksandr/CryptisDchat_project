#!/usr/bin/env bash
# Запуск всего CryptisDchat в Docker: создание всех контейнеров и Gunicorn внутри них.
#
#   scripts/run_gunicorn.sh            # = up: собрать образы, создать и запустить все контейнеры
#   scripts/run_gunicorn.sh status     # состояние каждого контейнера
#   scripts/run_gunicorn.sh logs [svc] # логи (например: logs backend)
#   scripts/run_gunicorn.sh down       # остановить (данные в томах сохраняются)
#
# Что делает `up` — создаёт и запускает все 11 контейнеров:
#   1. проверяет, что есть .env, worker.env, realm.env (их нужно заполнить заранее);
#   2. создаёт каталоги logs/ и data_warehouses/ — их монтируют контейнеры;
#   3. этап 1 — инфраструктура: db (MariaDB), redis, realm1..3; ждёт, пока db и redis станут healthy
#      (первая инициализация MariaDB со schema.sql может занять пару минут);
#   4. этап 2 — приложение:
#        backend — Gunicorn + UvicornWorker: FastAPI, WebSocket, Login.html/cryptis.html  (:3890)
#        admin   — Gunicorn + gthread: Flask-админка и вебхуки                             (:3891)
#        caddy   — HTTPS с самоподписанным сертификатом: приложение :3443, админка :3444
#        worker, worker_fast, beat — Celery;
#   5. ждёт, пока backend ответит на /healthz, и проверяет, что работают ВСЕ 11 контейнеров —
#      если какой-то упал, показывает его логи;
#   6. создаёт администратора Flask-панели из ADMIN_USERNAME / ADMIN_PASSWORD в .env.
#
# Контейнеры приложения работают от пользователя, запустившего скрипт (id -u / id -g), а не от root:
# так у них всегда есть права на запись в logs/ и data_warehouses/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mошибка:\033[0m %s\n' "$*" >&2; exit 1; }

INFRA=(db redis realm1 realm2 realm3)
APP=(backend admin caddy worker worker_fast beat)
ALL=("${INFRA[@]}" "${APP[@]}")

# владелец файлов в смонтированных каталогах — тот, кто запускает скрипт (подставляется в compose)
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"

check_files() {
  local missing=()
  for f in .env worker.env realm.env; do
    [[ -f "$f" ]] || missing+=("$f")
  done
  (( ${#missing[@]} == 0 )) || fail "нет файлов: ${missing[*]} — создайте их в корне проекта (см. README)"
  mkdir -p logs/celery logs/gunicorn data_warehouses/redis data_warehouses/uploads data_warehouses/backups
}

compose() {
  command -v docker >/dev/null 2>&1 || fail "Docker не установлен: https://docs.docker.com/get-docker/"
  docker compose version >/dev/null 2>&1 || fail "нужен Docker Compose v2 (команда 'docker compose')"
  docker compose "$@"
}

env_value() { grep -E "^$1=" .env | head -1 | cut -d= -f2-; }

# состояние сервиса: running / exited / restarting … и health: healthy / starting / unhealthy / пусто
state()  { compose ps -a --format '{{.State}}' "$1" 2>/dev/null | head -1; }
health() { compose ps -a --format '{{.Health}}' "$1" 2>/dev/null | head -1; }

wait_healthy() {  # сервис таймаут_секунд
  local svc=$1 limit=$2 waited=0
  while (( waited < limit )); do
    [[ "$(health "$svc")" == "healthy" ]] && return 0
    [[ "$(state "$svc")" =~ ^(exited|dead)$ ]] && break
    sleep 3
    waited=$((waited + 3))
  done
  compose logs --tail=40 "$svc" >&2 || true
  fail "контейнер $svc не стал healthy (состояние: $(state "$svc") $(health "$svc")) — логи выше"
}

check_all() {
  local failed=() svc st hl
  printf '\n  %-12s %-12s %s\n' "SERVICE" "STATE" "HEALTH"
  for svc in "${ALL[@]}"; do
    st="$(state "$svc")"
    hl="$(health "$svc")"
    printf '  %-12s %-12s %s\n' "$svc" "${st:-missing}" "${hl:--}"
    [[ "$st" == "running" && "$hl" != "unhealthy" ]] || failed+=("$svc")
  done
  echo
  if (( ${#failed[@]} )); then
    for svc in "${failed[@]}"; do
      printf '\033[1;31m── логи %s ──\033[0m\n' "$svc" >&2
      compose logs --tail=40 "$svc" >&2 || true
    done
    fail "не работают: ${failed[*]}"
  fi
}

up() {
  check_files

  say "этап 1: сборка образов и запуск инфраструктуры (${INFRA[*]})…"
  compose up -d --build "${INFRA[@]}"
  say "ожидание MariaDB (первая инициализация со schema.sql — до нескольких минут)…"
  wait_healthy db 300
  wait_healthy redis 60

  say "этап 2: запуск приложения (${APP[*]}) — Gunicorn в backend и admin…"
  compose up -d --build "${APP[@]}"

  say "ожидание backend…"
  for _ in $(seq 1 60); do
    curl -fsS http://localhost:3890/healthz >/dev/null 2>&1 && break
    sleep 2
  done
  sleep 5   # дать воркерам Celery время подключиться или упасть, чтобы проверка ниже была честной
  check_all
  curl -fsS http://localhost:3890/healthz >/dev/null 2>&1 || fail "backend не отвечает на /healthz"

  say "создание администратора Flask-панели…"
  compose exec -T admin python database/initial_data.py

  local https_host app_port admin_port
  https_host="$(env_value HTTPS_HOST || true)"
  app_port="$(env_value HTTPS_APP_PORT || true)"; app_port="${app_port:-3443}"
  admin_port="$(env_value HTTPS_ADMIN_PORT || true)"; admin_port="${admin_port:-3444}"

  cat <<EOF

  CryptisDchat запущен — все ${#ALL[@]} контейнеров работают
  ─ приложение:  https://${https_host}:${app_port}   (или http://localhost:3890 через SSH/VS Code)
  ─ админка:     https://${https_host}:${admin_port}/admin/   логин: $(env_value ADMIN_USERNAME)   пароль: $(env_value ADMIN_PASSWORD)
  ─ состояние:   scripts/run_gunicorn.sh status
  ─ остановка:   scripts/run_gunicorn.sh down

EOF
}

case "${1:-up}" in
  up)     up ;;
  status) check_all ;;
  logs)   shift; compose logs -f --tail=200 "$@" ;;
  down)   compose down ;;
  *)      echo "usage: $0 [up|status|logs [service]|down]" >&2; exit 64 ;;
esac
