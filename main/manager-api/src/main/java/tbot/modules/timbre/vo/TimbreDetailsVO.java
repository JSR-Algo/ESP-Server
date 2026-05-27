package tbot.modules.timbre.vo;

import java.io.Serializable;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Voice details displayVO
 * 
 * @author zjy
 * @since 2025-3-21
 */
@Data
public class TimbreDetailsVO implements Serializable {
    @Schema(description = "Voice id")
    private String id;

    @Schema(description = "Language")
    private String languages;

    @Schema(description = "Voice name")
    private String name;

    @Schema(description = "Notes")
    private String remark;

    @Schema(description = "Reference audio path")
    private String referenceAudio;

    @Schema(description = "Reference text")
    private String referenceText;

    @Schema(description = "Sort")
    private long sort;

    @Schema(description = "Corresponding TTS model primary key")
    private String ttsModelId;

    @Schema(description = "Voice code")
    private String ttsVoice;

    @Schema(description = "Audio playback URL")
    private String voiceDemo;

}
