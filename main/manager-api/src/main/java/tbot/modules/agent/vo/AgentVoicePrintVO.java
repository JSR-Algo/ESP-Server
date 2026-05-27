package tbot.modules.agent.vo;

import lombok.Data;

import java.util.Date;

/**
 * Show agent voiceprint listVO
 */
@Data
public class AgentVoicePrintVO {

    /**
     * Primary keyid
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
    /**
     * Create time
     */
    private Date createDate;
}
