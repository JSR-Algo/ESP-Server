package tbot.modules.agent.dto;

import java.io.Serializable;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Agent memory updateDTO
 */
@Data
@Schema(description = "Agent memory update object")
public class AgentMemoryDTO implements Serializable {
    private static final long serialVersionUID = 1L;

    @Schema(description = "Summary memory", example = "Build growable dynamic memory network, keep key info in limited space while intelligently maintaining info evolution path\n" +
            "Summarize important user info based on dialogue history to provide more personalized service in future conversations", required = false)
    private String summaryMemory;
}