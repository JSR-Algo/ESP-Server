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
# Only emit "Bearer …" when a shared token is configured. An empty "Bearer " header
# is treated as missing auth by NestJS and confuses debugging.
NESTJS_AUTH_HEADER=""
if [ -n "${NESTJS_TOKEN}" ]; then
  NESTJS_AUTH_HEADER="Bearer ${NESTJS_TOKEN}"
fi
# Escape sed replacement metacharacters in the token header (&, \, |).
NESTJS_AUTH_HEADER_ESCAPED=$(printf '%s' "${NESTJS_AUTH_HEADER}" | sed -e 's/[&|\\]/\\&/g')
sed -e "s|__NESTJS_UPSTREAM_HOST__|${NESTJS_UPSTREAM_HOST}|g" \
    -e "s|__NESTJS_UPSTREAM_SCHEME__|${NESTJS_UPSTREAM_SCHEME}|g" \
    -e "s|__NESTJS_AUTH_HEADER__|${NESTJS_AUTH_HEADER_ESCAPED}|g" \
    /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# 启动Nginx（前台运行保持容器存活）
nginx -g 'daemon off;'