package tbot.modules.agent.entity;

import java.math.BigDecimal;
import java.util.Date;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@TableName("ai_agent")
@Schema(description = "Agent info")
public class AgentEntity {

    @TableId(type = IdType.ASSIGN_UUID)
    @Schema(description = "Agent unique identifier")
    private String id;

    @Schema(description = "Owner user ID")
    private Long userId;

    @Schema(description = "Agent code")
    private String agentCode;

    @Schema(description = "Agent name")
    private String agentName;

    @Schema(description = "Speech recognition model identifier")
    private String asrModelId;

    @Schema(description = "Voice activity detection identifier")
    private String vadModelId;

    @Schema(description = "LLM identifier")
    private String llmModelId;

    @Schema(description = "Small model identifier")
    private String slmModelId;

    @Schema(description = "VLLM model identifier")
    private String vllmModelId;

    @Schema(description = "Speech synthesis model identifier")
    private String ttsModelId;

    @Schema(description = "Voice identifier")
    private String ttsVoiceId;

    @Schema(description = "Voice language")
    private String ttsLanguage;

    @Schema(description = "Voice mode")
    private String voiceMode;

    @Schema(description = "Google Live config JSON")
    private String googleLiveConfigJson;

    @Schema(description = "TTS volume")
    private Integer ttsVolume;

    @Schema(description = "TTS speed")
    private Integer ttsRate;

    @Schema(description = "TTS pitch")
    private Integer ttsPitch;

    @Schema(description = "Memory model identifier")
    private String memModelId;

    @Schema(description = "Intent model identifier")
    private String intentModelId;

    @Schema(description = "Chat history config (0 no recording, 1 text only, 2 text and voice)")
    private Integer chatHistoryConf;

    @Schema(description = "Role setting parameters")
    private String systemPrompt;

    @Schema(description = "Summary memory", example = "Build growable dynamic memory network, keep key info in limited space while intelligently maintaining info evolution path\n" +
            "Summarize important user info based on dialogue history to provide more personalized service in future conversations", required = false)
    private String summaryMemory;

    @Schema(description = "Language code")
    private String langCode;

    @Schema(description = "Interaction language")
    private String language;

    @Schema(description = "Sort")
    private Integer sort;

    @Schema(description = "Creator")
    private Long creator;

    @Schema(description = "Creation time")
    private Date createdAt;

    @Schema(description = "Updater")
    private Long updater;

    @Schema(description = "Update time")
    private Date updatedAt;
}
