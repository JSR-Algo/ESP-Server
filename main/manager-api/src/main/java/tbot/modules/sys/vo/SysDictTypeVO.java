package tbot.modules.sys.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

import java.io.Serializable;
import java.util.Date;

/**
 * Dictionary type VO
 */
@Data
@Schema(description = "Dictionary type VO")
public class SysDictTypeVO implements Serializable {
    @Schema(description = "Primary key")
    private Long id;

    @Schema(description = "Dictionary type")
    private String dictType;

    @Schema(description = "Dictionary name")
    private String dictName;

    @Schema(description = "Notes")
    private String remark;

    @Schema(description = "Sort")
    private Integer sort;

    @Schema(description = "Creator")
    private Long creator;

    @Schema(description = "Creator name")
    private String creatorName;

    @Schema(description = "Creation time")
    private Date createDate;

    @Schema(description = "Updater")
    private Long updater;

    @Schema(description = "Updater name")
    private String updaterName;

    @Schema(description = "Update time")
    private Date updateDate;
}
