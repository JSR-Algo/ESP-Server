package tbot.modules.agent.dto;

import java.io.Serializable;
import java.util.Map;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "Context source config DTO")
public class ContextProviderDTO implements Serializable {
    private static final long serialVersionUID = 1L;

    @Schema(description = "URL address")
    private String url;

    @Schema(description = "Request header")
    private Map<String, Object> headers;
}
