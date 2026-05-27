ALTER TABLE `ai_agent`
    ADD COLUMN `voice_mode` VARCHAR(32) NULL AFTER `tts_language`,
    ADD COLUMN `google_live_config_json` JSON NULL AFTER `voice_mode`;
