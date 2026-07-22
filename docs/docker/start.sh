#!/bin/bash
# 启动Java后端（docker内监听8003端口）
java -jar /app/tbot-esp32-api.jar \
  --server.port=8003 \
  --spring.datasource.druid.url=${SPRING_DATASOURCE_DRUID_URL} \
  --spring.datasource.druid.username=${SPRING_DATASOURCE_DRUID_USERNAME} \
  --spring.datasource.druid.password=${SPRING_DATASOURCE_DRUID_PASSWORD} \
  --spring.data.redis.host=${SPRING_DATA_REDIS_HOST} \
  --spring.data.redis.password=${SPRING_DATA_REDIS_PASSWORD} \
  --spring.data.redis.port=${SPRING_DATA_REDIS_PORT} &

# Render the /nestjs reverse-proxy upstream + optional shared token into the nginx
# config from env (configurable bridge: to repoint the course backend later, change
# NESTJS_UPSTREAM_HOST / NESTJS_TOKEN in the container env and restart -- no rebuild).
: "${NESTJS_UPSTREAM_HOST:=tbot-backend-8wmh.onrender.com}"
: "${NESTJS_UPSTREAM_SCHEME:=https}"
: "${NESTJS_TOKEN:=}"
: "${NESTJS_ADMIN_PROXY_KEY:=}"
# Only emit "Bearer …" when a shared token is configured. An empty "Bearer " header
# is treated as missing auth by NestJS and confuses debugging.
NESTJS_AUTH_HEADER=""
if [ -n "${NESTJS_TOKEN}" ]; then
  NESTJS_AUTH_HEADER="Bearer ${NESTJS_TOKEN}"
fi
# Escape sed replacement metacharacters in the token header (&, \, |).
NESTJS_AUTH_HEADER_ESCAPED=$(printf '%s' "${NESTJS_AUTH_HEADER}" | sed -e 's/[&|\\]/\\&/g')
NESTJS_ADMIN_PROXY_KEY_ESCAPED=$(printf '%s' "${NESTJS_ADMIN_PROXY_KEY}" \
  | sed -e 's/[&|\\]/\\&/g')

# HTTP Basic gate for /nestjs/. NESTJS_BASIC_HTPASSWD carries a ready-made
# htpasswd line ("user:$apr1$..."), so no password hashing tool is needed in the
# image and no credential is ever baked into a layer.
#
# The gate is REQUIRED whenever a shared NESTJS_TOKEN is configured: nginx adds
# that token to every proxied request, so an ungated /nestjs/ would grant the
# whole authoring API to anonymous callers. Fail closed rather than silently
# serving an open authoring bridge.
: "${NESTJS_BASIC_HTPASSWD:=}"
if [ -n "${NESTJS_BASIC_HTPASSWD}" ]; then
  printf '%s\n' "${NESTJS_BASIC_HTPASSWD}" > /etc/nginx/.nestjs_htpasswd
  chmod 600 /etc/nginx/.nestjs_htpasswd
  NESTJS_BASIC_REALM='"TBOT authoring"'
else
  if [ -n "${NESTJS_TOKEN}" ]; then
    echo "start.sh: NESTJS_TOKEN is set but NESTJS_BASIC_HTPASSWD is empty — refusing to expose an unauthenticated authoring bridge" >&2
    exit 1
  fi
  : > /etc/nginx/.nestjs_htpasswd
  NESTJS_BASIC_REALM='off'
fi

sed -e "s|__NESTJS_UPSTREAM_HOST__|${NESTJS_UPSTREAM_HOST}|g" \
    -e "s|__NESTJS_UPSTREAM_SCHEME__|${NESTJS_UPSTREAM_SCHEME}|g" \
    -e "s|__NESTJS_AUTH_HEADER__|${NESTJS_AUTH_HEADER_ESCAPED}|g" \
    -e "s|__NESTJS_ADMIN_PROXY_KEY__|${NESTJS_ADMIN_PROXY_KEY_ESCAPED}|g" \
    -e "s|__NESTJS_BASIC_REALM__|${NESTJS_BASIC_REALM}|g" \
    /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# 启动Nginx（前台运行保持容器存活）
nginx -g 'daemon off;'
