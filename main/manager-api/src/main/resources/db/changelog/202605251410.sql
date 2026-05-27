-- Remove remaining Chinese defaults from runtime agent plugin/model payloads.
-- This keeps newly migrated databases aligned with the English/Vietnamese TBOT setup.

UPDATE `ai_agent_plugin_mapping`
SET `param_info` = JSON_SET(
  CAST(`param_info` AS JSON),
  '$.default_location',
  'Ho Chi Minh City'
)
WHERE `plugin_id` = 'SYSTEM_PLUGIN_WEATHER'
  AND JSON_VALID(`param_info`);

UPDATE `ai_agent_plugin_mapping`
SET `param_info` = JSON_SET(
  CAST(`param_info` AS JSON),
  '$.news_sources',
  'The Paper;Baidu Hot Search;Cailian Press'
)
WHERE `plugin_id` = 'SYSTEM_PLUGIN_NEWS_NEWSNOW'
  AND JSON_VALID(`param_info`);

UPDATE `ai_model_config`
SET `config_json` = JSON_SET(
  CAST(`config_json` AS JSON),
  '$.api_key',
  'your_api_key',
  '$.reference_text',
  JSON_ARRAY('Reference text')
)
WHERE `id` IN ('LLM_ChatGLMLLM', 'VLLM_ChatGLMVLLM')
  AND JSON_VALID(`config_json`);
