package tbot.modules.sys.dto;

import java.io.Serializable;
import java.util.Date;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Null;
import lombok.Data;
import tbot.common.utils.DateUtils;
import tbot.common.validator.group.AddGroup;
import tbot.common.validator.group.DefaultGroup;
import tbot.common.validator.group.UpdateGroup;

/**
 * Dictionary type
 */
@Data
@Schema(description = "Dictionary type")
public class SysDictTypeDTO implements Serializable {

    @Schema(description = "id")
    @Null(message = "{id.null}", groups = AddGroup.class)
    @NotNull(message = "{id.require}", groups = UpdateGroup.class)
    private Long id;

    @Schema(description = "Dictionary type")
    @NotBlank(message = "{sysdict.type.require}", groups = DefaultGroup.class)
    private String dictType;

    @Schema(description = "Dictionary name")
    @NotBlank(message = "{sysdict.name.require}", groups = DefaultGroup.class)
    private String dictName;

    @Schema(description = "Notes")
    private String remark;

    @Schema(description = "Sort")
    @Min(value = 0, message = "{sort.number}", groups = DefaultGroup.class)
    private Integer sort;

    @Schema(description = "Creation time")
    @JsonProperty(access = JsonProperty.Access.READ_ONLY)
    @JsonFormat(pattern = DateUtils.DATE_TIME_PATTERN)
    private Date createDate;

    @Schema(description = "Update time")
    @JsonProperty(access = JsonProperty.Access.READ_ONLY)
    @JsonFormat(pattern = DateUtils.DATE_TIME_PATTERN)
    private Date updateDate;
}