-- Enforce non-Chinese defaults for new installs and migrated databases.
-- Keep historical changelogs immutable; this final seed pass removes old
-- Chinese defaults from operational config tables only.
SET @han_pattern = _utf8mb4'[一-龥]' COLLATE utf8mb4_bin;

UPDATE `ai_model_config`
SET
  `config_json` = JSON_SET(
    CAST(`config_json` AS JSON),
    '$.voice',
    'vi-VN-HoaiMyNeural'
  ),
  `model_name` = 'Edge Speech Synthesis',
  `remark` = 'Default Edge TTS voice uses Vietnamese Hoai My. Change the voice in admin if needed.'
WHERE `id` = 'TTS_EdgeTTS'
  AND JSON_VALID(`config_json`);

UPDATE `ai_agent_template`
SET
  `agent_code` = 'TBOT',
  `asr_model_id` = 'ASR_OpenaiASR',
  `tts_model_id` = 'TTS_EdgeTTS',
  `tts_voice_id` = 'TTS_EdgeTTS0012',
  `agent_name` = 'TBOT Assistant',
  `system_prompt` = 'You are TBOT, a concise, friendly Vietnamese assistant. Reply in Vietnamese by default unless the user asks for another language.',
  `lang_code` = 'vi',
  `language` = 'Vietnamese'
WHERE `asr_model_id` = 'ASR_FunASR'
   OR `tts_voice_id` IN ('TTS_EdgeTTS0001', 'TTS_EdgeTTS0002', 'TTS_EdgeTTS0003', 'TTS_EdgeTTS0004', 'TTS_EdgeTTS0005', 'TTS_EdgeTTS0006')
   OR `lang_code` IN ('zh', 'zh_CN', 'zh_TW')
   OR `agent_code` IN ('Xiaozhi', 'xiaozhi')
   OR CONVERT(`agent_code` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(`agent_name` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(`system_prompt` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(`language` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern;

UPDATE `ai_agent`
SET
  `agent_name` = CASE
    WHEN `agent_code` IN ('Xiaozhi', 'xiaozhi') OR CONVERT(`agent_code` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern OR CONVERT(`agent_name` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN 'TBOT Assistant'
    ELSE `agent_name`
  END,
  `system_prompt` = CASE
    WHEN `agent_code` IN ('Xiaozhi', 'xiaozhi') OR CONVERT(`agent_code` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern OR CONVERT(`system_prompt` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN 'You are TBOT, a concise, friendly Vietnamese assistant. Reply in Vietnamese by default unless the user asks for another language.'
    ELSE `system_prompt`
  END,
  `agent_code` = CASE WHEN `agent_code` IN ('Xiaozhi', 'xiaozhi') OR CONVERT(`agent_code` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN 'TBOT' ELSE `agent_code` END,
  `asr_model_id` = CASE WHEN `asr_model_id` = 'ASR_FunASR' THEN 'ASR_OpenaiASR' ELSE `asr_model_id` END,
  `tts_model_id` = CASE WHEN `tts_model_id` = 'TTS_EdgeTTS' THEN 'TTS_EdgeTTS' ELSE `tts_model_id` END,
  `tts_voice_id` = CASE WHEN `tts_voice_id` IN ('TTS_EdgeTTS0001', 'TTS_EdgeTTS0002', 'TTS_EdgeTTS0003', 'TTS_EdgeTTS0004', 'TTS_EdgeTTS0005', 'TTS_EdgeTTS0006') THEN 'TTS_EdgeTTS0012' ELSE `tts_voice_id` END,
  `lang_code` = CASE WHEN `lang_code` IN ('zh', 'zh_CN', 'zh_TW') THEN 'vi' ELSE `lang_code` END,
  `language` = CASE WHEN `language` IN ('Chinese', 'Mandarin') OR CONVERT(`language` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN 'Vietnamese' ELSE `language` END,
  `voice_mode` = CASE WHEN `voice_mode` IS NULL OR `voice_mode` = '' OR `voice_mode` = 'classic_pipeline' THEN 'google_live' ELSE `voice_mode` END,
  `google_live_config_json` = CASE
    WHEN `google_live_config_json` IS NULL THEN JSON_OBJECT(
      'model', 'gemini-2.5-flash-native-audio-preview-12-2025',
      'language_code', 'vi-VN',
      'enable_audio_input', true,
      'enable_audio_output', true,
      'native_voice', true
    )
    ELSE `google_live_config_json`
  END;

UPDATE `ai_model_provider`
SET
  `name` = CONCAT(`model_type`, ' ', `provider_code`),
  `fields` = CASE `model_type`
    WHEN 'LLM' THEN CAST('[{"key":"api_key","label":"API key","type":"string"},{"key":"base_url","label":"Base URL","type":"string"},{"key":"model_name","label":"Model name","type":"string"}]' AS JSON)
    WHEN 'ASR' THEN CAST('[{"key":"api_key","label":"API key","type":"string"},{"key":"base_url","label":"Base URL","type":"string"},{"key":"model_name","label":"Model name","type":"string"}]' AS JSON)
    WHEN 'TTS' THEN CAST('[{"key":"api_key","label":"API key","type":"string"},{"key":"base_url","label":"Base URL","type":"string"},{"key":"voice","label":"Voice","type":"string"}]' AS JSON)
    WHEN 'VLLM' THEN CAST('[{"key":"api_key","label":"API key","type":"string"},{"key":"base_url","label":"Base URL","type":"string"},{"key":"model_name","label":"Model name","type":"string"}]' AS JSON)
    ELSE CAST('[]' AS JSON)
  END
WHERE CONVERT(`name` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(CAST(`fields` AS CHAR) USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern;

UPDATE `ai_model_config`
SET
  `model_name` = CASE WHEN CONVERT(`model_name` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN CONCAT(`model_code`, ' ', `model_type`) ELSE `model_name` END,
  `remark` = CASE WHEN CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN NULL ELSE `remark` END,
  `config_json` = CASE
    WHEN JSON_VALID(`config_json`) AND CONVERT(CAST(`config_json` AS CHAR) USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN JSON_SET(CAST(`config_json` AS JSON), '$.reference_text', JSON_ARRAY('Reference text'))
    ELSE `config_json`
  END
WHERE CONVERT(`model_name` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(CAST(`config_json` AS CHAR) USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern;

DELETE FROM `ai_tts_voice`
WHERE `id` NOT IN ('TTS_EdgeTTS0012', 'TTS_EdgeTTS0013')
  AND `tts_model_id` NOT IN ('TTS_GeminiTTS', 'TTS_Gemini25ProTTS')
  AND (
    CONVERT(`name` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
    OR CONVERT(`languages` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
    OR CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
    OR CONVERT(`reference_text` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
  );

UPDATE `sys_params`
SET
  `param_value` = CASE `param_code`
    WHEN 'wakeup_words' THEN 'Hello TBOT;Hey TBOT;TBOT'
    WHEN 'exit_commands' THEN 'exit;close;stop'
    WHEN 'plugins.get_weather.default_location' THEN 'Ho Chi Minh City'
    WHEN 'plugins.home_assistant.devices' THEN 'Living room,Desk lamp,switch.example_light'
    WHEN 'plugins.home_assistant.api_key' THEN 'your_home_assistant_api_token'
    WHEN 'system_error_response' THEN 'TBOT is busy right now. Please try again later.'
    WHEN 'end_prompt.prompt' THEN 'End the conversation warmly and concisely.'
    ELSE `param_value`
  END,
  `remark` = CASE
    WHEN CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN CONCAT('Parameter: ', `param_code`)
    ELSE `remark`
  END
WHERE CONVERT(`param_value` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern;

UPDATE `sys_dict_type`
SET
  `dict_name` = `dict_type`,
  `remark` = NULL
WHERE CONVERT(`dict_name` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern;

UPDATE `sys_dict_data`
SET
  `dict_label` = CASE `dict_value`
    WHEN '+86' THEN 'China mainland'
    WHEN '+852' THEN 'Hong Kong'
    WHEN '+853' THEN 'Macau'
    WHEN '+886' THEN 'Taiwan'
    ELSE CONCAT('Option ', `id`)
  END,
  `remark` = CASE
    WHEN CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern THEN NULL
    ELSE `remark`
  END
WHERE CONVERT(`dict_label` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern
   OR CONVERT(`remark` USING utf8mb4) COLLATE utf8mb4_bin REGEXP @han_pattern;
