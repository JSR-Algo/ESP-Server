package tbot.modules.agent.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * TBOT device chat report request
 *
 * @author Haotian
 * @version 1.0, 2025/5/8
 */
@Data
@Schema(description = "TBOT device chat report request")
public class AgentChatHistoryReportDTO {
    @Schema(description = "MAC address", example = "00:11:22:33:44:55")
    @NotBlank
    private String macAddress;
    @Schema(description = "Session ID", example = "79578c31-f1fb-426a-900e-1e934215f05a")
    @NotBlank
    private String sessionId;
    @Schema(description = "Message type: 1-user, 2-agent", example = "1")
    @NotNull
    private Byte chatType;
    @Schema(description = "Chat content", example = "Hello")
    @NotBlank
    private String content;
    @Schema(description = "Base64-encoded Opus audio data", example = "")
    private String audioBase64;
    @Schema(description = "Report time, 10-digit timestamp; defaults to current time when empty", example = "1745657732")
    private Long reportTime;
}
