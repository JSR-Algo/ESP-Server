package tbot.modules.voiceclone.dto;

import java.util.List;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "Voice clone DTO")
public class VoiceCloneDTO {

    @Schema(description = "Model ID")
    private String modelId;

    @Schema(description = "Voice ID list")
    private List<String> voiceIds;

    @Schema(description = "User ID")
    private Long userId;

    @Schema(description = "Language")
    private String languages;
}
