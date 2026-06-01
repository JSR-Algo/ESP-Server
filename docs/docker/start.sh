#!/usr/bin/env bash
set -euo pipefail

# Start Java backend in background
java -jar /app/tbot-esp32-api.jar &
JAVA_PID=$!

# Start Nginx in foreground
nginx -g 'daemon off;' &
NGINX_PID=$!

# Wait for any process to exit
wait -n

# Exit with the code of the first process to fail
exit $?
