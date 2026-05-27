package tbot.modules.sys.vo;

import java.util.Date;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * AdminPaginationShow userVO
 * @ zjy
 * 
 * @since 2025-3-25
 */
@Data
public class AdminPageUserVO {

    @Schema(description = "Device count")
    private String deviceCount;

    @Schema(description = "Phone number")
    private String mobile;

    @Schema(description = "Status")
    private Integer status;

    @Schema(description = "User id")
    private String userid;

    @Schema(description = "Registration time")
    private Date createDate;
}
