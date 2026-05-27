package tbot.modules.device.dto;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.AllArgsConstructor;
import lombok.Data;

/**
 * Device-boundDTO
 * 
 * @author zjy
 * @since 2025-3-28
 */
@Data
@AllArgsConstructor
@Schema(description = "Device connection header info")
public class DeviceBindDTO {

    @Schema(description = "mac address")
    private String macAddress;

    @Schema(description = "Owner user id")
    private Long userId;

    @Schema(description = "Agent id")
    private String agentId;

}