#!/bin/sh
# Собирает /tmp/proxysql.cnf из шаблона и переменных окружения и запускает ProxySQL.
# --initial: конфигурация каждый раз берётся из файла, а не из сохранённой proxysql.db,
# поэтому смена пароля в .env применяется простым перезапуском контейнера.
set -eu

: "${DB_USER:?set DB_USER in .env}"
: "${DB_PASSWORD:?set DB_PASSWORD in .env}"
: "${DB_NAME:?set DB_NAME in .env}"
ADMIN_PASSWORD="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
export ADMIN_PASSWORD

# подстановка без регулярных выражений: спецсимволы в пароле не ломают результат
awk '
function put(s, key, value,    i, out) {
    out = ""
    while ((i = index(s, key)) > 0) {
        out = out substr(s, 1, i - 1) value
        s = substr(s, i + length(key))
    }
    return out s
}
{
    line = put($0, "@DB_USER@", ENVIRON["DB_USER"])
    line = put(line, "@DB_PASSWORD@", ENVIRON["DB_PASSWORD"])
    line = put(line, "@DB_NAME@", ENVIRON["DB_NAME"])
    line = put(line, "@ADMIN_PASSWORD@", ENVIRON["ADMIN_PASSWORD"])
    print line
}' /etc/proxysql/proxysql.cnf.template > /tmp/proxysql.cnf
chmod 600 /tmp/proxysql.cnf

exec proxysql -f --initial -c /tmp/proxysql.cnf -D /var/lib/proxysql
