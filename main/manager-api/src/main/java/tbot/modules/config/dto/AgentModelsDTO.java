package tbot.modules.config.dto;

import java.util.Map;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
@Schema(description = "Get agent model config DTO")
public class AgentModelsDTO {

    @NotBlank(message = "Device MAC address cannot be empty")
    @Schema(description = "Device MAC address")
    private String macAddress;

    @NotBlank(message = "Client ID cannot be empty")
    @Schema(description = "Client ID")
    private String clientId;

    @NotNull(message = "Client-instantiated model cannot be empty")
    @Schema(description = "Models instantiated by client")
    private Map<String, String> selectedModule;
}