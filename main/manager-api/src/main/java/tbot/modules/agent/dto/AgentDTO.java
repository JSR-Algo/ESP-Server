package tbot.modules.agent.dto;

import java.util.Date;
import java.util.List;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;
import tbot.modules.agent.dto.AgentTagDTO;

/**
 * Agent data transfer object
 * Used to pass agent-related data between service layer and controller layer
 */
@Data
@Schema(description = "Agent object")
public class AgentDTO {
    @Schema(description = "Agent code", example = "AGT_1234567890")
    private String id;

    @Schema(description = "Agent name", example = "Customer service assistant")
    private String agentName;

    @Schema(description = "Speech synthesis model name", example = "tts_model_01")
    private String ttsModelName;

    @Schema(description = "Voice name", example = "voice_01")
    private String ttsVoiceName;

    @Schema(description = "Voice mode", example = "google_live")
    private String voiceMode;

    @Schema(description = "Google Live config JSON", example = "{\"voice\":\"Kore\"}")
    private String googleLiveConfigJson;

    @Schema(description = "LLM name", example = "llm_model_01")
    private String llmModelName;

    @Schema(description = "Vision model name", example = "vllm_model_01")
    private String vllmModelName;

    @Schema(description = "Memory model ID", example = "mem_model_01")
    private String memModelId;

    @Schema(description = "Role setting parameters", example = "You are a professional customer service assistant, responsible for answering user questions and providing help")
    private String systemPrompt;

    @Schema(description = "Summary memory", example = "Build growable dynamic memory network, keep key info in limited space while intelligently maintaining info evolution path\n" +
            "Summarize important user info based on dialogue history to provide more personalized service in future conversations", required = false)
    private String summaryMemory;

    @Schema(description = "Last connection time", example = "2024-03-20 10:00:00")
    private Date lastConnectedAt;

    @Schema(description = "Device count", example = "10")
    private Integer deviceCount;

    @Schema(description = "Tag list")
    private List<AgentTagDTO> tags;
}
