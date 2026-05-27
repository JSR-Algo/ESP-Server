package tbot.modules.timbre.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import lombok.Data;

/**
 * Voice table dataDTO
 * 
 * @author zjy
 * @since 2025-3-21
 */
@Data
@Schema(description = "Voice table info")
public class TimbreDataDTO {

    @Schema(description = "Language")
    @NotBlank(message = "{timbre.languages.require}")
    private String languages;

    @Schema(description = "Voice name")
    @NotBlank(message = "{timbre.name.require}")
    private String name;

    @Schema(description = "Notes")
    private String remark;

    @Schema(description = "Reference audio path")
    private String referenceAudio;

    @Schema(description = "Reference text")
    private String referenceText;

    @Schema(description = "Sort")
    @Min(value = 0, message = "{sort.number}")
    private long sort;

    @Schema(description = "Corresponding TTS model primary key")
    @NotBlank(message = "{timbre.ttsModelId.require}")
    private String ttsModelId;

    @Schema(description = "Voice code")
    @NotBlank(message = "{timbre.ttsVoice.require}")
    private String ttsVoice;

    @Schema(description = "Audio playback URL")
    private String voiceDemo;
}