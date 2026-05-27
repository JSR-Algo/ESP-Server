-- Google Gemini model-config seed.
-- Only registers provider types that have runtime support in tbot-server:
-- LLM: core/providers/llm/gemini/gemini.py
-- TTS: core/providers/tts/gemini.py

DELETE FROM `ai_tts_voice` WHERE `tts_model_id` IN ('TTS_GeminiTTS', 'TTS_Gemini25ProTTS');
DELETE FROM `ai_model_config` WHERE `id` IN (
    'LLM_Gemini31ProPreview',
    'LLM_Gemini3FlashPreview',
    'LLM_Gemini31FlashLite',
    'LLM_Gemini25Pro',
    'LLM_Gemini25Flash',
    'LLM_Gemini25FlashLite',
    'LLM_Gemini20FlashLite',
    'TTS_GeminiTTS',
    'TTS_Gemini25ProTTS'
);
DELETE FROM `ai_model_provider` WHERE `id` = 'SYSTEM_TTS_gemini';

UPDATE `ai_model_provider` SET
    `name` = 'Google Gemini',
    `fields` = '[{"key":"api_key","label":"APIAPI key","type":"string"},{"key":"model_name","label":"Model name","type":"string"},{"key":"http_proxy","label":"HTTPProxy","type":"string"},{"key":"https_proxy","label":"HTTPSProxy","type":"string"}]',
    `updater` = 1,
    `update_date` = NOW()
WHERE `id` = 'SYSTEM_LLM_gemini';

UPDATE `ai_model_config` SET
    `model_name` = 'Google Gemini 2.0 Flash',
    `config_json` = '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-2.0-flash", "http_proxy": "", "https_proxy": ""}',
    `doc_link` = 'https://ai.google.dev/gemini-api/docs/models',
    `remark` = 'Google Gemini 2.0 Flash General-purpose low-latency model. Get API key at Google AI Studio: https://aistudio.google.com/apikey',
    `sort` = 15,
    `updater` = 1,
    `update_date` = NOW()
WHERE `id` = 'LLM_GeminiLLM';

INSERT INTO `ai_model_config` (`id`, `model_type`, `model_code`, `model_name`, `is_default`, `is_enabled`, `config_json`, `doc_link`, `remark`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('LLM_Gemini31ProPreview', 'LLM', 'Gemini31ProPreview', 'Google Gemini 3.1 Pro Preview', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-3.1-pro-preview", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/models', 'Latest Gemini Pro preview model for complex reasoning, multimodality, and agent/coding.', 16, 1, NOW(), 1, NOW()),
('LLM_Gemini3FlashPreview', 'LLM', 'Gemini3FlashPreview', 'Google Gemini 3 Flash Preview', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-3-flash-preview", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/models', 'Latest Gemini Flash preview model for low-latency chat and tool use.', 17, 1, NOW(), 1, NOW()),
('LLM_Gemini31FlashLite', 'LLM', 'Gemini31FlashLite', 'Google Gemini 3.1 Flash-Lite', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-3.1-flash-lite", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/models', 'Latest Gemini Flash-Lite for high-concurrency, low-cost tasks.', 18, 1, NOW(), 1, NOW()),
('LLM_Gemini25Pro', 'LLM', 'Gemini25Pro', 'Google Gemini 2.5 Pro', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-2.5-pro", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/models', 'Gemini 2.5 Pro Stable model for complex reasoning and long context.', 19, 1, NOW(), 1, NOW()),
('LLM_Gemini25Flash', 'LLM', 'Gemini25Flash', 'Google Gemini 2.5 Flash', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-2.5-flash", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/models', 'Gemini 2.5 Flash Stable model balancing quality, speed, and cost.', 20, 1, NOW(), 1, NOW()),
('LLM_Gemini25FlashLite', 'LLM', 'Gemini25FlashLite', 'Google Gemini 2.5 Flash-Lite', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-2.5-flash-lite", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/models', 'Gemini 2.5 Flash-Lite Stable, low-cost model.', 21, 1, NOW(), 1, NOW()),
('LLM_Gemini20FlashLite', 'LLM', 'Gemini20FlashLite', 'Google Gemini 2.0 Flash-Lite', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-2.0-flash-lite", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/models', 'Gemini 2.0 Flash-Lite Low-cost model.', 22, 1, NOW(), 1, NOW());

INSERT INTO `ai_model_provider` (`id`, `model_type`, `provider_code`, `name`, `fields`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES (
    'SYSTEM_TTS_gemini', 'TTS', 'gemini', 'Google Gemini TTS',
    '[{"key":"api_key","label":"APIAPI key","type":"string"},{"key":"model_name","label":"Model name","type":"string"},{"key":"voice","label":"Voice","type":"string"},{"key":"style_instructions","label":"Style prompt","type":"string"},{"key":"output_dir","label":"Output directory","type":"string"},{"key":"http_proxy","label":"HTTPProxy","type":"string"},{"key":"https_proxy","label":"HTTPSProxy","type":"string"}]',
    21, 1, NOW(), 1, NOW()
);

INSERT INTO `ai_model_config` (`id`, `model_type`, `model_code`, `model_name`, `is_default`, `is_enabled`, `config_json`, `doc_link`, `remark`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('TTS_GeminiTTS', 'TTS', 'GeminiTTS', 'Google Gemini 2.5 Flash TTS', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-2.5-flash-preview-tts", "voice": "Kore", "style_instructions": "", "output_dir": "tmp/", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/speech-generation', 'Gemini 2.5 Flash TTS preview，Reuses Gemini API key; outputs 24kHz PCM wrapped in WAV.', 24, 1, NOW(), 1, NOW()),
('TTS_Gemini25ProTTS', 'TTS', 'Gemini25ProTTS', 'Google Gemini 2.5 Pro TTS', 0, 1, '{"type": "gemini", "api_key": "your_api_key", "model_name": "gemini-2.5-pro-preview-tts", "voice": "Kore", "style_instructions": "", "output_dir": "tmp/", "http_proxy": "", "https_proxy": ""}', 'https://ai.google.dev/gemini-api/docs/speech-generation', 'Gemini 2.5 Pro TTS preview，Suitable for higher-quality speech synthesis.', 25, 1, NOW(), 1, NOW());

INSERT INTO `ai_tts_voice` (`id`, `tts_model_id`, `name`, `tts_voice`, `languages`, `voice_demo`, `remark`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('TTS_GeminiTTS_0001', 'TTS_GeminiTTS', 'Zephyr', 'Zephyr', 'Tiếng Việt', NULL, 'Bright', 1, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0002', 'TTS_GeminiTTS', 'Puck', 'Puck', 'Tiếng Việt', NULL, 'Upbeat', 2, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0003', 'TTS_GeminiTTS', 'Charon', 'Charon', 'Tiếng Việt', NULL, 'Informative', 3, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0004', 'TTS_GeminiTTS', 'Kore', 'Kore', 'Tiếng Việt', NULL, 'Firm', 4, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0005', 'TTS_GeminiTTS', 'Fenrir', 'Fenrir', 'Tiếng Việt', NULL, 'Excitable', 5, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0006', 'TTS_GeminiTTS', 'Leda', 'Leda', 'Tiếng Việt', NULL, 'Youthful', 6, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0007', 'TTS_GeminiTTS', 'Orus', 'Orus', 'Tiếng Việt', NULL, 'Firm', 7, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0008', 'TTS_GeminiTTS', 'Aoede', 'Aoede', 'Tiếng Việt', NULL, 'Breezy', 8, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0009', 'TTS_GeminiTTS', 'Callirrhoe', 'Callirrhoe', 'Tiếng Việt', NULL, 'Easy-going', 9, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0010', 'TTS_GeminiTTS', 'Autonoe', 'Autonoe', 'Tiếng Việt', NULL, 'Bright', 10, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0011', 'TTS_GeminiTTS', 'Enceladus', 'Enceladus', 'Tiếng Việt', NULL, 'Breathy', 11, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0012', 'TTS_GeminiTTS', 'Iapetus', 'Iapetus', 'Tiếng Việt', NULL, 'Clear', 12, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0013', 'TTS_GeminiTTS', 'Umbriel', 'Umbriel', 'Tiếng Việt', NULL, 'Easy-going', 13, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0014', 'TTS_GeminiTTS', 'Algieba', 'Algieba', 'Tiếng Việt', NULL, 'Smooth', 14, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0015', 'TTS_GeminiTTS', 'Despina', 'Despina', 'Tiếng Việt', NULL, 'Smooth', 15, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0016', 'TTS_GeminiTTS', 'Erinome', 'Erinome', 'Tiếng Việt', NULL, 'Clear', 16, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0017', 'TTS_GeminiTTS', 'Algenib', 'Algenib', 'Tiếng Việt', NULL, 'Gravelly', 17, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0018', 'TTS_GeminiTTS', 'Rasalgethi', 'Rasalgethi', 'Tiếng Việt', NULL, 'Informative', 18, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0019', 'TTS_GeminiTTS', 'Laomedeia', 'Laomedeia', 'Tiếng Việt', NULL, 'Upbeat', 19, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0020', 'TTS_GeminiTTS', 'Achernar', 'Achernar', 'Tiếng Việt', NULL, 'Soft', 20, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0021', 'TTS_GeminiTTS', 'Alnilam', 'Alnilam', 'Tiếng Việt', NULL, 'Firm', 21, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0022', 'TTS_GeminiTTS', 'Schedar', 'Schedar', 'Tiếng Việt', NULL, 'Even', 22, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0023', 'TTS_GeminiTTS', 'Gacrux', 'Gacrux', 'Tiếng Việt', NULL, 'Mature', 23, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0024', 'TTS_GeminiTTS', 'Pulcherrima', 'Pulcherrima', 'Tiếng Việt', NULL, 'Forward', 24, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0025', 'TTS_GeminiTTS', 'Achird', 'Achird', 'Tiếng Việt', NULL, 'Friendly', 25, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0026', 'TTS_GeminiTTS', 'Zubenelgenubi', 'Zubenelgenubi', 'Tiếng Việt', NULL, 'Casual', 26, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0027', 'TTS_GeminiTTS', 'Vindemiatrix', 'Vindemiatrix', 'Tiếng Việt', NULL, 'Gentle', 27, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0028', 'TTS_GeminiTTS', 'Sadachbia', 'Sadachbia', 'Tiếng Việt', NULL, 'Lively', 28, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0029', 'TTS_GeminiTTS', 'Sadaltager', 'Sadaltager', 'Tiếng Việt', NULL, 'Knowledgeable', 29, 1, NOW(), 1, NOW()),
('TTS_GeminiTTS_0030', 'TTS_GeminiTTS', 'Sulafat', 'Sulafat', 'Tiếng Việt', NULL, 'Warm', 30, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0001', 'TTS_Gemini25ProTTS', 'Zephyr', 'Zephyr', 'Tiếng Việt', NULL, 'Bright', 1, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0002', 'TTS_Gemini25ProTTS', 'Puck', 'Puck', 'Tiếng Việt', NULL, 'Upbeat', 2, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0003', 'TTS_Gemini25ProTTS', 'Charon', 'Charon', 'Tiếng Việt', NULL, 'Informative', 3, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0004', 'TTS_Gemini25ProTTS', 'Kore', 'Kore', 'Tiếng Việt', NULL, 'Firm', 4, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0005', 'TTS_Gemini25ProTTS', 'Fenrir', 'Fenrir', 'Tiếng Việt', NULL, 'Excitable', 5, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0006', 'TTS_Gemini25ProTTS', 'Leda', 'Leda', 'Tiếng Việt', NULL, 'Youthful', 6, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0007', 'TTS_Gemini25ProTTS', 'Orus', 'Orus', 'Tiếng Việt', NULL, 'Firm', 7, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0008', 'TTS_Gemini25ProTTS', 'Aoede', 'Aoede', 'Tiếng Việt', NULL, 'Breezy', 8, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0009', 'TTS_Gemini25ProTTS', 'Callirrhoe', 'Callirrhoe', 'Tiếng Việt', NULL, 'Easy-going', 9, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0010', 'TTS_Gemini25ProTTS', 'Autonoe', 'Autonoe', 'Tiếng Việt', NULL, 'Bright', 10, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0011', 'TTS_Gemini25ProTTS', 'Enceladus', 'Enceladus', 'Tiếng Việt', NULL, 'Breathy', 11, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0012', 'TTS_Gemini25ProTTS', 'Iapetus', 'Iapetus', 'Tiếng Việt', NULL, 'Clear', 12, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0013', 'TTS_Gemini25ProTTS', 'Umbriel', 'Umbriel', 'Tiếng Việt', NULL, 'Easy-going', 13, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0014', 'TTS_Gemini25ProTTS', 'Algieba', 'Algieba', 'Tiếng Việt', NULL, 'Smooth', 14, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0015', 'TTS_Gemini25ProTTS', 'Despina', 'Despina', 'Tiếng Việt', NULL, 'Smooth', 15, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0016', 'TTS_Gemini25ProTTS', 'Erinome', 'Erinome', 'Tiếng Việt', NULL, 'Clear', 16, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0017', 'TTS_Gemini25ProTTS', 'Algenib', 'Algenib', 'Tiếng Việt', NULL, 'Gravelly', 17, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0018', 'TTS_Gemini25ProTTS', 'Rasalgethi', 'Rasalgethi', 'Tiếng Việt', NULL, 'Informative', 18, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0019', 'TTS_Gemini25ProTTS', 'Laomedeia', 'Laomedeia', 'Tiếng Việt', NULL, 'Upbeat', 19, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0020', 'TTS_Gemini25ProTTS', 'Achernar', 'Achernar', 'Tiếng Việt', NULL, 'Soft', 20, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0021', 'TTS_Gemini25ProTTS', 'Alnilam', 'Alnilam', 'Tiếng Việt', NULL, 'Firm', 21, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0022', 'TTS_Gemini25ProTTS', 'Schedar', 'Schedar', 'Tiếng Việt', NULL, 'Even', 22, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0023', 'TTS_Gemini25ProTTS', 'Gacrux', 'Gacrux', 'Tiếng Việt', NULL, 'Mature', 23, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0024', 'TTS_Gemini25ProTTS', 'Pulcherrima', 'Pulcherrima', 'Tiếng Việt', NULL, 'Forward', 24, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0025', 'TTS_Gemini25ProTTS', 'Achird', 'Achird', 'Tiếng Việt', NULL, 'Friendly', 25, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0026', 'TTS_Gemini25ProTTS', 'Zubenelgenubi', 'Zubenelgenubi', 'Tiếng Việt', NULL, 'Casual', 26, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0027', 'TTS_Gemini25ProTTS', 'Vindemiatrix', 'Vindemiatrix', 'Tiếng Việt', NULL, 'Gentle', 27, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0028', 'TTS_Gemini25ProTTS', 'Sadachbia', 'Sadachbia', 'Tiếng Việt', NULL, 'Lively', 28, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0029', 'TTS_Gemini25ProTTS', 'Sadaltager', 'Sadaltager', 'Tiếng Việt', NULL, 'Knowledgeable', 29, 1, NOW(), 1, NOW()),
('TTS_Gemini25Pro_0030', 'TTS_Gemini25ProTTS', 'Sulafat', 'Sulafat', 'Tiếng Việt', NULL, 'Warm', 30, 1, NOW(), 1, NOW());
