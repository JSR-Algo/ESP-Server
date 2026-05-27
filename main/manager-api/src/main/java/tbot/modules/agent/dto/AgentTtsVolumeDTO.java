package tbot.modules.agent.dto;

import java.io.Serializable;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import lombok.Data;

@Data
@Schema(description = "Agent TTS volume update object")
public class AgentTtsVolumeDTO implements Serializable {
    private static final long serialVersionUID = 1L;

    @Min(value = 0, message = "TTS volume must be between 0 and 100")
    @Max(value = 100, message = "TTS volume must be between 0 and 100")
    @Schema(description = "TTS volume", example = "50", requiredMode = Schema.RequiredMode.REQUIRED)
    private Integer ttsVolume;
}
