-- T5.3 — provision the simulated device in manager-api.
--
-- The ESP lesson server refuses to serve a WebSocket for a MAC it cannot resolve to a
-- bound device + agent: with an empty `ai_device`/`ai_agent` the connection is accepted
-- and then closed immediately with code 1000 and NO server-side log line, which is
-- extremely hard to diagnose from the client side. The lesson-studio seed only creates
-- an admin `sys_user`, so a stack seeded with it alone can never run a lesson.
--
-- Real robots get these rows from the OTA activation/claim flow. Simulation has no
-- firmware to activate, so the device is provisioned directly and deterministically.

-- ---------------------------------------------------------------------------
-- F-T53-05 workaround: the hello-ack param is still named `xiaozhi` in a freshly
-- migrated manager-api database, but the ESP server reads `config["tbot"]`
-- unguarded (core/connection.py:411). Every device connection therefore dies with
-- `KeyError: 'tbot'` on a clean deployment. The long-lived lab database works only
-- because the row was renamed there by hand. Values are byte-identical; only the
-- key differs. The real fix is a manager-api migration and is routed in §5 --
-- this rename only makes the simulated stack bootable.
-- ---------------------------------------------------------------------------
UPDATE sys_params SET param_code = 'tbot' WHERE param_code = 'xiaozhi';

SET @sim_user   := 9000001;                    -- lesson_admin_e2e, from seed-mysql.sql
SET @sim_agent  := 'agent_e2e_sim_0001';
SET @sim_device := 'device_e2e_sim_0001';
SET @sim_mac    := '14:c1:9f:d1:a8:48';        -- must match LESSON_SIM_DEVICE_ID

-- ---------------------------------------------------------------------------
-- `tts_model_id` is NOT optional decoration, even though this agent runs
-- google_live and never synthesises a word through EdgeTTS.
--
-- With it NULL, manager-api answers /config/agent-models with a selected_module
-- that has no TTS key. `normalize_voice_config` then injects a `TTS` block into
-- that private config unconditionally (config_loader.py `_apply_tts_runtime_overrides`
-- always writes `TTS.EdgeTTS`), which flips the guard at connection.py:1546 true —
-- and the very next line reads `private_config["selected_module"]["TTS"]`, which was
-- never written. Every connection therefore died with
-- `Voice session initialization failed: 'TTS'` BEFORE the voice provider was built,
-- so the whole google_live path was silently skipped in simulation and the runtime
-- ran with no provider at all.
--
-- Setting it here provisions the agent the way a real claimed robot is provisioned.
-- The google_live branch returns before `initialize_modules`, so no TTS module is
-- ever instantiated and no network TTS call is made — voice_mode stays google_live.
-- The unguarded read itself is a product defect and is routed as F-T53-12.
-- ---------------------------------------------------------------------------
INSERT INTO ai_agent (
  id, user_id, agent_code, agent_name, voice_mode, google_live_config_json,
  tts_model_id, tts_voice_id,
  system_prompt, chat_history_conf, lang_code, language, sort,
  creator, created_at, updater, updated_at
) VALUES (
  @sim_agent, @sim_user, 'E2E_SIM', 'Lesson E2E Sim Agent', 'google_live',
  -- Empty key: the lesson-admission path is a pure string classifier and never dials
  -- the Live client, so simulation needs no Gemini credential.
  JSON_OBJECT('api_key', '', 'model', 'gemini-3.1-flash-live-preview', 'voice_name', 'Kore'),
  'TTS_EdgeTTS', 'TTS_EdgeTTS_0001',
  'You are TeeBot, a friendly English tutor for children.', 1, 'vi', 'vi-VN', 0,
  @sim_user, NOW(), @sim_user, NOW()
)
ON DUPLICATE KEY UPDATE
  voice_mode = VALUES(voice_mode),
  google_live_config_json = VALUES(google_live_config_json),
  tts_model_id = VALUES(tts_model_id),
  tts_voice_id = VALUES(tts_voice_id),
  updated_at = NOW();

INSERT INTO ai_device (
  id, user_id, mac_address, last_connected_at, auto_update, board, alias,
  child_name, child_age, child_profile_revision, agent_id, app_version, sort,
  creator, create_date, updater, update_date
) VALUES (
  @sim_device, @sim_user, @sim_mac, NOW(), 0, 'lcdwiki-es3c35p', 'E2E Sim Robot',
  'Mai', 6, 1, @sim_agent, '2.2.89', 0,
  @sim_user, NOW(), @sim_user, NOW()
)
ON DUPLICATE KEY UPDATE
  agent_id = VALUES(agent_id),
  mac_address = VALUES(mac_address),
  child_name = VALUES(child_name),
  update_date = NOW();
