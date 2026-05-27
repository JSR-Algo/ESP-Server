package tbot.modules.agent.dto;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Agent updateDTO
 * Dedicated toUpdate agent,idField required, used to identify agent to update
 * Other fields optional, only update provided fields
 */
@Data
@Schema(description = "Agent update object")
public class AgentUpdateDTO implements Serializable {
    private static final long serialVersionUID = 1L;

    @Schema(description = "Agent code", example = "AGT_1234567890", nullable = true)
    private String agentCode;

    @Schema(description = "Agent name", example = "Customer service assistant", nullable = true)
    private String agentName;

    @Schema(description = "Speech recognition model identifier", example = "asr_model_02", nullable = true)
    private String asrModelId;

    @Schema(description = "Voice activity detection identifier", example = "vad_model_02", nullable = true)
    private String vadModelId;

    @Schema(description = "LLM identifier", example = "llm_model_02", nullable = true)
    private String llmModelId;

    @Schema(description = "Small model identifier", example = "slm_model_02", nullable = true)
    private String slmModelId;

    @Schema(description = "VLLM model identifier", example = "vllm_model_02", required = false)
    private String vllmModelId;

    @Schema(description = "Speech synthesis model identifier", example = "tts_model_02", required = false)
    private String ttsModelId;

    @Schema(description = "Voice identifier", example = "voice_02", nullable = true)
    private String ttsVoiceId;

    @Schema(description = "Voice language", example = "Mandarin", nullable = true)
    private String ttsLanguage;

    @Schema(description = "Voice mode", example = "google_live", nullable = true)
    private String voiceMode;

    @Schema(description = "Google Live config JSON", example = "{\"voice\":\"Kore\"}", nullable = true)
    private String googleLiveConfigJson;

    @Schema(description = "TTS volume", example = "50", nullable = true)
    private Integer ttsVolume;

    @Schema(description = "TTS speed", example = "50", nullable = true)
    private Integer ttsRate;

    @Schema(description = "TTS pitch", example = "50", nullable = true)
    private Integer ttsPitch;

    @Schema(description = "Memory model identifier", example = "mem_model_02", nullable = true)
    private String memModelId;

    @Schema(description = "Intent model identifier", example = "intent_model_02", nullable = true)
    private String intentModelId;

    @Schema(description = "Plugin function info", nullable = true)
    private List<FunctionInfo> functions;

    @Schema(description = "Role setting parameters", example = "You are a professional customer service assistant, responsible for answering user questions and providing help", nullable = true)
    private String systemPrompt;

    @Schema(description = "Summary memory", example = "Build growable dynamic memory network, keep key info in limited space while intelligently maintaining info evolution path\n"
            + "Summarize important user info based on dialogue history to provide more personalized service in future conversations", nullable = true)
    private String summaryMemory;

    @Schema(description = "Chat history config (0 no recording, 1 text only, 2 text and voice)", example = "3", nullable = true)
    private Integer chatHistoryConf;

    @Schema(description = "Language code", example = "zh_CN", nullable = true)
    private String langCode;

    @Schema(description = "Interaction language", example = "Chinese", nullable = true)
    private String language;

    @Schema(description = "Sort", example = "1", nullable = true)
    private Integer sort;

    @Schema(description = "Context source config", nullable = true)
    private List<ContextProviderDTO> contextProviders;

    @Schema(description = "Replacement word file ID list", nullable = true)
    private List<String> correctWordFileIds;

    @Data
    @Schema(description = "Plugin function info")
    public static class FunctionInfo implements Serializable {
        @Schema(description = "Plugin ID", example = "plugin_01")
        private String pluginId;

        @Schema(description = "Function parameter info", nullable = true)
        private HashMap<String, Object> paramInfo;

        private static final long serialVersionUID = 1L;
    }
}
