-- Apply current lab quick-tunnel URLs to manager-api Parameter Management.
-- Usage:
--   docker exec -i tbot-runtime-db sh -lc 'mysql -uroot -p123456 tbot_esp32_server' \
--     < robot/esp32-server/deploy/current-quick-tunnel-sys-params.sql

UPDATE sys_params
SET param_value = 'https://warranty-thunder-independence-related.trycloudflare.com'
WHERE param_code = 'server.fronted_url';

UPDATE sys_params
SET param_value = 'wss://freebsd-concern-noon-cement.trycloudflare.com/tbot/v1/'
WHERE param_code = 'server.websocket';

UPDATE sys_params
SET param_value = 'https://carefully-freelance-improving-numerical.trycloudflare.com/tbot/ota/'
WHERE param_code = 'server.ota';

SELECT param_code, param_value
FROM sys_params
WHERE param_code IN ('server.fronted_url', 'server.websocket', 'server.ota')
ORDER BY id;
