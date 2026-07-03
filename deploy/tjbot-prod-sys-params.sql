-- Apply stable production URLs for tjbot.vn to manager-api Parameter Management.
-- Usage on the VPS:
--   docker exec -i tbot-esp32-server-db sh -lc 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" tbot_esp32_server' \
--     < deploy/tjbot-prod-sys-params.sql

UPDATE sys_params
SET param_value = 'https://admin.tjbot.vn'
WHERE param_code = 'server.fronted_url';

UPDATE sys_params
SET param_value = 'wss://esp.tjbot.vn/tbot/v1/'
WHERE param_code = 'server.websocket';

UPDATE sys_params
SET param_value = 'https://esp.tjbot.vn/tbot/ota/'
WHERE param_code = 'server.ota';

SELECT param_code, param_value
FROM sys_params
WHERE param_code IN ('server.fronted_url', 'server.websocket', 'server.ota')
ORDER BY id;
