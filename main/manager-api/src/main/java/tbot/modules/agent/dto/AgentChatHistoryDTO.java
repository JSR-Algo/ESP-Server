package tbot.modules.agent.dto;

import java.util.Date;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Agent chat recordsDTO
 */
@Data
@Schema(description = "Agent chat records")
public class AgentChatHistoryDTO {
    @Schema(description = "Creation time")
    private Date createdAt;

    @Schema(description = "Message type: 1-user, 2-agent")
    private Byte chatType;

    @Schema(description = "Chat content")
    private String content;

    @Schema(description = "Audio ID")
    private String audioId;

    @Schema(description = "MAC address")
    private String macAddress;
}