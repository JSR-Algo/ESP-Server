package tbot.modules.device.dto;

import java.util.Map;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class DeviceToolsCallReqDTO {

    @NotBlank(message = "Tool name cannot be empty")
    private String name;

    private Map<String, Object> arguments;
}