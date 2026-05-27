package tbot.modules.device.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "User display device list VO")
public class UserShowDeviceListVO {

    @Schema(description = "app version")
    private String appVersion;

    @Schema(description = "Bound user name")
    private String bindUserName;

    @Schema(description = "Device model")
    private String deviceType;

    @Schema(description = "Device unique identifier")
    private String id;

    @Schema(description = "mac address")
    private String macAddress;

    @Schema(description = "Enable OTA")
    private Integer otaUpgrade;

    @Schema(description = "Last conversation time")
    private String recentChatTime;

}