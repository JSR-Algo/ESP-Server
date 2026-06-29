-- Seed firmware-facing backend API base URL for OTA/device config responses.
INSERT INTO `sys_params` (id, param_code, param_value, value_type, param_type, remark)
VALUES (108, 'server.api_url', 'https://tbot-backend-8wmh.onrender.com/v1', 'string', 1, 'backend API base URL for firmware device config and lesson runtime')
ON DUPLICATE KEY UPDATE
  param_value = VALUES(param_value),
  value_type = VALUES(value_type),
  param_type = VALUES(param_type),
  remark = VALUES(remark);
