package tbot.modules.agent.dto;

import lombok.Data;

/**
 * Save agent voiceprintdto
 *
 * @author zjy
 */
@Data
public class AgentVoicePrintSaveDTO {
    /**
     * Associated agentid
     */
    private String agentId;
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
