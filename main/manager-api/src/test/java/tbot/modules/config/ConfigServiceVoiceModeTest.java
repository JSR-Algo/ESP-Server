package tbot.modules.config;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import tbot.modules.config.service.impl.ConfigServiceImpl;

class ConfigServiceVoiceModeTest {

    @Test
    @DisplayName("appendVoiceModeConfig serializes google live mode and config")
    void appendVoiceModeConfigSerializesGoogleLiveMode() throws Exception {
        ConfigServiceImpl service = new ConfigServiceImpl(null, null, null, null, null, null, null, null, null, null,
                null, null, null);
        Map<String, Object> result = new HashMap<>();

        invokeAppendVoiceModeConfig(service, "google_live", "{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\"}", result);

        Map<?, ?> voiceMode = assertInstanceOf(Map.class, result.get("voice_mode"));
        assertEquals("google_live", voiceMode.get("type"));
        assertEquals(true, voiceMode.get("fallback_to_classic_on_error"));
        Map<?, ?> googleLive = assertInstanceOf(Map.class, result.get("google_live"));
        assertEquals("gemini-2.5-flash-native-audio-preview-12-2025", googleLive.get("model"));
    }

    @Test
    @DisplayName("appendVoiceModeConfig propagates fallback flag from google live config")
    void appendVoiceModeConfigPropagatesFallbackFlag() throws Exception {
        ConfigServiceImpl service = new ConfigServiceImpl(null, null, null, null, null, null, null, null, null, null,
                null, null, null);
        Map<String, Object> result = new HashMap<>();

        invokeAppendVoiceModeConfig(
                service,
                "google_live",
                "{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\",\"fallback_to_classic_on_error\":false}",
                result);

        Map<?, ?> voiceMode = assertInstanceOf(Map.class, result.get("voice_mode"));
        assertEquals("google_live", voiceMode.get("type"));
        assertEquals(false, voiceMode.get("fallback_to_classic_on_error"));
        Map<?, ?> googleLive = assertInstanceOf(Map.class, result.get("google_live"));
        assertEquals(false, googleLive.get("fallback_to_classic_on_error"));
    }

    @Test
    @DisplayName("appendVoiceModeConfig defaults blank mode to classic pipeline")
    void appendVoiceModeConfigDefaultsBlankMode() throws Exception {
        ConfigServiceImpl service = new ConfigServiceImpl(null, null, null, null, null, null, null, null, null, null,
                null, null, null);
        Map<String, Object> result = new HashMap<>();

        invokeAppendVoiceModeConfig(service, " ", null, result);

        Map<?, ?> voiceMode = assertInstanceOf(Map.class, result.get("voice_mode"));
        assertEquals("classic_pipeline", voiceMode.get("type"));
        assertEquals(true, voiceMode.get("fallback_to_classic_on_error"));
        assertTrue(!result.containsKey("google_live"));
    }

    @Test
    @DisplayName("appendVoiceModeConfig clamps unknown mode to classic pipeline")
    void appendVoiceModeConfigClampsUnknownMode() throws Exception {
        ConfigServiceImpl service = new ConfigServiceImpl(null, null, null, null, null, null, null, null, null, null,
                null, null, null);
        Map<String, Object> result = new HashMap<>();

        invokeAppendVoiceModeConfig(service, "future_mode", "{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\"}", result);

        Map<?, ?> voiceMode = assertInstanceOf(Map.class, result.get("voice_mode"));
        assertEquals("classic_pipeline", voiceMode.get("type"));
        assertEquals(true, voiceMode.get("fallback_to_classic_on_error"));
        assertTrue(!result.containsKey("google_live"));
    }

    @Test
    @DisplayName("appendVoiceModeConfig skips malformed google live config")
    void appendVoiceModeConfigSkipsMalformedGoogleLiveConfig() {
        ConfigServiceImpl service = new ConfigServiceImpl(null, null, null, null, null, null, null, null, null, null,
                null, null, null);
        Map<String, Object> result = new HashMap<>();

        assertDoesNotThrow(() -> invokeAppendVoiceModeConfig(service, "google_live", "{bad json", result));

        Map<?, ?> voiceMode = assertInstanceOf(Map.class, result.get("voice_mode"));
        assertEquals("google_live", voiceMode.get("type"));
        assertEquals(true, voiceMode.get("fallback_to_classic_on_error"));
        assertTrue(!result.containsKey("google_live"));
    }

    @Test
    @DisplayName("appendVoiceModeConfig does not emit stale google live config for classic mode")
    void appendVoiceModeConfigSkipsStaleGoogleLiveConfigForClassicMode() throws Exception {
        ConfigServiceImpl service = new ConfigServiceImpl(null, null, null, null, null, null, null, null, null, null,
                null, null, null);
        Map<String, Object> result = new HashMap<>();

        invokeAppendVoiceModeConfig(service, "classic_pipeline", "{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\"}", result);

        Map<?, ?> voiceMode = assertInstanceOf(Map.class, result.get("voice_mode"));
        assertEquals("classic_pipeline", voiceMode.get("type"));
        assertEquals(true, voiceMode.get("fallback_to_classic_on_error"));
        assertTrue(!result.containsKey("google_live"));
    }

    @Test
    @DisplayName("buildModuleConfig appends voice mode payload for google live mode")
    void buildModuleConfigAppendsVoiceModePayload() throws Exception {
        ConfigServiceImpl service = new ConfigServiceImpl(null, null, null, null, null, null, null, null, null, null,
                null, null, null);
        Map<String, Object> result = new HashMap<>();

        invokeBuildModuleConfig(service, "google_live", "{\"model\":\"gemini-2.5-flash-native-audio-preview-12-2025\"}", result);

        Map<?, ?> voiceMode = assertInstanceOf(Map.class, result.get("voice_mode"));
        assertEquals("google_live", voiceMode.get("type"));
        assertEquals(true, voiceMode.get("fallback_to_classic_on_error"));
        Map<?, ?> googleLive = assertInstanceOf(Map.class, result.get("google_live"));
        assertEquals("gemini-2.5-flash-native-audio-preview-12-2025", googleLive.get("model"));
    }

    private static void invokeAppendVoiceModeConfig(
            ConfigServiceImpl service,
            String voiceModeValue,
            String googleLiveConfigJson,
            Map<String, Object> result) throws Exception {
        Method method = ConfigServiceImpl.class.getDeclaredMethod(
                "appendVoiceModeConfig",
                String.class,
                String.class,
                Map.class);
        method.setAccessible(true);
        method.invoke(service, voiceModeValue, googleLiveConfigJson, result);
    }

    private static void invokeBuildModuleConfig(
            ConfigServiceImpl service,
            String voiceModeValue,
            String googleLiveConfigJson,
            Map<String, Object> result) throws Exception {
        Method method = ConfigServiceImpl.class.getDeclaredMethod(
                "buildModuleConfig",
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                Integer.class,
                Integer.class,
                Integer.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                String.class,
                Map.class,
                boolean.class);
        method.setAccessible(true);
        method.invoke(service,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                voiceModeValue,
                googleLiveConfigJson,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                result,
                false);
    }
}
