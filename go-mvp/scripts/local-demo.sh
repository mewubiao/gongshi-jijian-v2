#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v initdb >/dev/null && command -v pg_config >/dev/null; then
  export PATH="$(pg_config --bindir):$PATH"
fi
for tool in go initdb pg_ctl createdb; do
  command -v "$tool" >/dev/null || { echo "缺少 ${tool}；请安装 Go 1.26+ 与 PostgreSQL 15+，或使用 docker compose up --build"; exit 1; }
done
mkdir -p .pg
pg_dir="$PWD/.pg/data"
started_here=false
cleanup() { if "$started_here"; then pg_ctl -D "$pg_dir" -m fast -w stop; fi; }
trap cleanup EXIT
if [[ ! -f "$pg_dir/PG_VERSION" ]]; then
  printf '%s\n' 'local-demo-only' > .pg/password
  chmod 600 .pg/password
  initdb -D "$pg_dir" -U workshop -A scram-sha-256 --pwfile=.pg/password --encoding=UTF8 --no-locale
fi
if ! pg_ctl -D "$pg_dir" status >/dev/null; then
  pg_ctl -D "$pg_dir" -l "$PWD/.pg/postgres.log" -o "-h 127.0.0.1 -p 55432 -c unix_socket_directories=''" -w start
  started_here=true
fi
export PGPASSWORD=local-demo-only
db_exists=$(psql -h 127.0.0.1 -p 55432 -U workshop -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='workshop'")
if [[ "$db_exists" != "1" ]]; then
  createdb -h 127.0.0.1 -p 55432 -U workshop workshop
fi
export DATABASE_URL='postgres://workshop:local-demo-only@127.0.0.1:55432/workshop?sslmode=disable'
export LISTEN_ADDR="${LISTEN_ADDR:-127.0.0.1:8080}"
echo "演示地址 http://${LISTEN_ADDR}；Ctrl+C 停止应用并停止本脚本启动的 PostgreSQL，数据保留在 .pg/"
go run . -migrate -seed -demo
