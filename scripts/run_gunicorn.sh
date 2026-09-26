#!/usr/bin/env bash
# Запуск всего CryptisDchat в Docker: создание всех контейнеров и Gunicorn внутри них.
#
#   scripts/run_gunicorn.sh            # = up: собрать образы, создать и запустить все контейнеры
#   scripts/run_gunicorn.sh check      # только проверить .env и порты, ничего не запуская
#   scripts/run_gunicorn.sh status     # состояние каждого контейнера
#   scripts/run_gunicorn.sh logs [svc] # логи (например: logs backend)
#   scripts/run_gunicorn.sh down       # остановить (данные в томах сохраняются)
#
# Что делает `up` — создаёт и запускает все 11 контейнеров:
#   1. проверяет, что есть .env, worker.env, realm.env (их нужно заполнить заранее);
#   2. проверяет .env (обязательные переменные, повторы, заглушки change-me-*, согласованность
#      адресов) и что порты хоста не заняты чужими программами;
#      создаёт каталоги logs/ и data_warehouses/ — их монтируют контейнеры;
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

env_value() { grep -E "^$1=" "${2:-.env}" | head -1 | cut -d= -f2- | tr -d '\r'; }

# обязательные переменные .env и секреты, которые нельзя оставлять заглушками change-me-*
REQUIRED=(APP_ENV HTTPS_HOST PUBLIC_ORIGIN ALLOWED_WS_ORIGINS TON_PROOF_DOMAIN
          DB_NAME DB_USER DB_PASSWORD DB_ROOT_PASSWORD
          JWT_SECRET TON_PROOF_SECRET ADMIN_SECRET_KEY ADMIN_USERNAME ADMIN_PASSWORD WEBHOOK_SECRET)
SECRETS=(DB_PASSWORD DB_ROOT_PASSWORD JWT_SECRET TON_PROOF_SECRET ADMIN_SECRET_KEY ADMIN_PASSWORD WEBHOOK_SECRET)

check_env() {
  local problems=() k v host app_port origin ws
  for k in "${REQUIRED[@]}"; do
    v="$(env_value "$k" || true)"
    [[ -n "$v" ]] && continue
    if [[ "$k" == HTTPS_HOST ]]; then
      problems+=("HTTPS_HOST — нет в .env: внешний IP или имя сервера, например echo 'HTTPS_HOST=203.0.113.10' >> .env")
    else
      problems+=("$k — нет в .env или пустая")
    fi
  done
  for k in "${SECRETS[@]}"; do
    v="$(env_value "$k" || true)"
    [[ "$v" != change-me* ]] || problems+=("$k — заглушка change-me-*; сгенерируйте: openssl rand -hex 32")
  done
  for k in HTTPS_APP_PORT HTTPS_ADMIN_PORT DB_EXTERNAL_PORT; do
    v="$(env_value "$k" || true)"
    [[ -z "$v" || "$v" =~ ^[0-9]+$ ]] || problems+=("$k=$v — должен быть номером порта")
  done
  [[ -n "$(env_value REALM_API_TOKEN realm.env || true)" ]] || problems+=("REALM_API_TOKEN — пустой в realm.env")
  # повторы: скрипт берёт первое значение, а docker compose — последнее
  for k in $(grep -oE '^[A-Z_][A-Z0-9_]*=' .env | tr -d '=' | sort | uniq -d); do
    problems+=("$k — задана в .env несколько раз (строки $(grep -nE "^$k=" .env | cut -d: -f1 | paste -sd, -)); оставьте одну")
  done

  # адрес приложения должен совпадать во всех местах, иначе не работают вход и WebSocket
  host="$(env_value HTTPS_HOST || true)"
  app_port="$(env_value HTTPS_APP_PORT || true)"; app_port="${app_port:-3443}"
  if [[ -n "$host" ]]; then
    origin="https://${host}:${app_port}"
    [[ "$(env_value PUBLIC_ORIGIN || true)" == "$origin" ]] \
      || problems+=("PUBLIC_ORIGIN должен быть $origin (sed -i 's|^PUBLIC_ORIGIN=.*|PUBLIC_ORIGIN=$origin|' .env)")
    ws="$(env_value ALLOWED_WS_ORIGINS || true)"
    [[ ",${ws}," == *",${origin},"* ]] \
      || problems+=("ALLOWED_WS_ORIGINS должен содержать $origin (через запятую)")
    [[ "$(env_value TON_PROOF_DOMAIN || true)" == "${host}:${app_port}" ]] \
      || problems+=("TON_PROOF_DOMAIN должен быть ${host}:${app_port} (sed -i 's|^TON_PROOF_DOMAIN=.*|TON_PROOF_DOMAIN=${host}:${app_port}|' .env)")
  fi

  if (( ${#problems[@]} )); then
    printf '\033[1;31m.env:\033[0m\n' >&2
    printf '  - %s\n' "${problems[@]}" >&2
    fail "исправьте .env (${#problems[@]} шт.) и запустите снова"
  fi
  say ".env в порядке"
}

# порты хоста: занятые НЕ нашими контейнерами — конфликт (как «Bind for :::3306 failed»)
check_ports() {
  if ! command -v ss >/dev/null 2>&1; then
    say "ss не найден — проверка портов пропущена"
    return 0
  fi
  local ours busy=() p owner v
  ours="$(compose ps -q 2>/dev/null || true)"
  local ports=("$(v="$(env_value DB_EXTERNAL_PORT || true)"; echo "${v:-3307}")" 3890 3891 6390 8096
               "$(v="$(env_value HTTPS_APP_PORT || true)"; echo "${v:-3443}")"
               "$(v="$(env_value HTTPS_ADMIN_PORT || true)"; echo "${v:-3444}")")
  for p in "${ports[@]}"; do
    ss -ltnH "sport = :$p" 2>/dev/null | grep -q . || continue
    owner="$(docker ps --no-trunc -q --filter "publish=$p" 2>/dev/null | head -1)"
    [[ -n "$owner" && -n "$ours" && "$ours" == *"$owner"* ]] && continue   # наш же контейнер
    busy+=("$p")
  done
  if (( ${#busy[@]} )); then
    for p in "${busy[@]}"; do
      printf '  порт %s занят: %s\n' "$p" \
        "$(sudo -n ss -ltnpH "sport = :$p" 2>/dev/null | grep -o 'users:.*' | head -1 || true)" >&2
    done
    fail "порты заняты: ${busy[*]} — освободите их или смените порт в .env (DB_EXTERNAL_PORT, HTTPS_APP_PORT, HTTPS_ADMIN_PORT) / docker-compose.yml"
  fi
  say "порты в порядке (свободны или заняты нашими контейнерами): ${ports[*]}"
}

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
  check_env
  check_ports

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
  check)  check_files; check_env; check_ports ;;
  status) check_all ;;
  logs)   shift; compose logs -f --tail=200 "$@" ;;
  down)   compose down ;;
  *)      echo "usage: $0 [up|check|status|logs [service]|down]" >&2; exit 64 ;;
esac
