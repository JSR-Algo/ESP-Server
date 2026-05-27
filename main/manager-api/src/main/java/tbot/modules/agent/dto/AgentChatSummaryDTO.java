package tbot.modules.agent.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Agent chat recordsSummaryDTO
 */
@Data
@Schema(description = "Agent chat history summary object")
public class AgentChatSummaryDTO {

    @Schema(description = "Session ID")
    private String sessionId;

    @Schema(description = "Agent ID")
    private String agentId;

    @Schema(description = "Summary content")
    private String summary;

    @Schema(description = "Summary status")
    private boolean success;

    @Schema(description = "Error info")
    private String errorMessage;

    public AgentChatSummaryDTO() {
        this.success = true;
    }

    public AgentChatSummaryDTO(String sessionId, String agentId, String summary) {
        this.sessionId = sessionId;
        this.agentId = agentId;
        this.summary = summary;
        this.success = true;
    }

    public AgentChatSummaryDTO(String sessionId, String errorMessage) {
        this.sessionId = sessionId;
        this.errorMessage = errorMessage;
        this.success = false;
    }

}