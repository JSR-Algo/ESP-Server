package tbot.modules.agent.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

import java.io.Serializable;
import java.util.List;

@Data
@Schema(description = "Agent tag DTO")
public class AgentTagDTO implements Serializable {

    private static final long serialVersionUID = 1L;

    @Schema(description = "Tag ID")
    private String id;

    @Schema(description = "Tag name")
    private String tagName;
}
