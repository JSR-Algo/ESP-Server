package tbot.modules.agent.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * agent user personal chat dataVO
 */
@Data
public class AgentChatHistoryUserVO {
    @Schema(description = "Chat content")
    private String content;

    @Schema(description = "Audio ID")
    private String audioId;
}
