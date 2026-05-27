package tbot.modules.agent.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import lombok.Data;

/**
 * Object returned by voiceprint recognition API
 */
@Data
public class IdentifyVoicePrintResponse {
    /**
     * Best matching voiceprintid
     */
    @JsonProperty("speaker_id")
    private String speakerId;
    /**
     * Voiceprint score
     */
    private Double score;
}
