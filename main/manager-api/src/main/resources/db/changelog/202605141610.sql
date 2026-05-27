-- Add Vietnamese Edge TTS voices so role-config language select can choose Tiếng Việt.
-- Voice codes follow Microsoft Azure Speech language and voice support.

DELETE FROM `ai_tts_voice`
WHERE `id` IN ('TTS_EdgeTTS0012', 'TTS_EdgeTTS0013');

INSERT INTO `ai_tts_voice` (`id`, `tts_model_id`, `name`, `tts_voice`, `languages`, `voice_demo`, `remark`, `reference_audio`, `reference_text`, `sort`, `creator`, `create_date`, `updater`, `update_date`) VALUES
('TTS_EdgeTTS0012', 'TTS_EdgeTTS', 'EdgeTTS nữ-Hoài My', 'vi-VN-HoaiMyNeural', 'Tiếng Việt', NULL, 'Vietnamese female voice', NULL, NULL, 12, 1, NOW(), 1, NOW()),
('TTS_EdgeTTS0013', 'TTS_EdgeTTS', 'EdgeTTS nam-Nam Minh', 'vi-VN-NamMinhNeural', 'Tiếng Việt', NULL, 'Vietnamese male voice', NULL, NULL, 13, 1, NOW(), 1, NOW());
