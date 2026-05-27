package tbot.modules.agent.dto;

import lombok.Data;

/**
 * Modify agent voiceprintdto
 *
 * @author zjy
 */
@Data
public class AgentVoicePrintUpdateDTO {
    /**
     * Agent voiceprintid
     */
    private String id;
    /**
     * Audio Fileid
     */
    private String audioId;
    /**
     * Person name of voiceprint source
     */
    private String sourceName;
    /**
     * Describe person of voiceprint source
     */
    private String introduce;
}
