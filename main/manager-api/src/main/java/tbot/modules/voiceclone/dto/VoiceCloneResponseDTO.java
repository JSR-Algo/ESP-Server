package tbot.modules.voiceclone.dto;

import java.util.Date;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Voice clone response DTO
 * For frontend displayVoice cloningInfo, includeModel nameandUser name
 */
@Data
@Schema(description = "Voice clone response DTO")
public class VoiceCloneResponseDTO {

    @Schema(description = "Unique identifier")
    private String id;

    @Schema(description = "Voice name")
    private String name;

    @Schema(description = "Model id")
    private String modelId;

    @Schema(description = "Model name")
    private String modelName;

    @Schema(description = "Voice id")
    private String voiceId;

    @Schema(description = "Language")
    private String languages;

    @Schema(description = "User ID (linked user table)")
    private Long userId;

    @Schema(description = "User name")
    private String userName;

    @Schema(description = "Training status: 0 pending 1 training 2 success 3 failed")
    private Integer trainStatus;

    @Schema(description = "Training error reason")
    private String trainError;

    @Schema(description = "Creation time")
    private Date createDate;

    @Schema(description = "Whether audio data exists")
    private Boolean hasVoice;
}