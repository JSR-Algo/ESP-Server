#!/bin/sh
set -eu

PORT="${PORT:-10000}"
: "${TBOT_SERVER_HOST:?set TBOT_SERVER_HOST}"

sed \
  -e "s|__PORT__|${PORT}|g" \
  -e "s|__TBOT_SERVER_HOST__|${TBOT_SERVER_HOST}|g" \
  /usr/local/etc/haproxy/render-haproxy.cfg.template \
  > /usr/local/etc/haproxy/haproxy.cfg

exec haproxy -f /usr/local/etc/haproxy/haproxy.cfg
