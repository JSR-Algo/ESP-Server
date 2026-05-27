package tbot.modules.agent.dto;

import java.time.LocalDateTime;

import lombok.Data;

/**
 * Agent session listDTO
 */
@Data
public class AgentChatSessionDTO {
    /**
     * SessionID
     */
    private String sessionId;

    /**
     * Session Time
     */
    private LocalDateTime createdAt;

    /**
     * Chat Count
     */
    private Integer chatCount;

    /**
     * Session Title
     */
    private String title;
}