package tbot.modules.sys.dto;

import java.io.Serializable;
import java.util.Date;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;

import io.swagger.v3.oas.annotations.media.Schema;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Null;
import jakarta.validation.constraints.Pattern;
import lombok.Data;
import tbot.common.utils.DateUtils;
import tbot.common.validator.group.AddGroup;
import tbot.common.validator.group.DefaultGroup;
import tbot.common.validator.group.UpdateGroup;

/**
 * Parameter management
 */
@Data
@Schema(description = "Parameter management")
public class SysParamsDTO implements Serializable {

    @Schema(description = "id")
    @Null(message = "{id.null}", groups = AddGroup.class)
    @NotNull(message = "{id.require}", groups = UpdateGroup.class)
    private Long id;

    @Schema(description = "Parameter code")
    @NotBlank(message = "{sysparams.paramcode.require}", groups = DefaultGroup.class)
    private String paramCode;

    @Schema(description = "Parameter value")
    @NotBlank(message = "{sysparams.paramvalue.require}", groups = DefaultGroup.class)
    private String paramValue;

    @Schema(description = "Value type")
    @NotBlank(message = "{sysparams.valuetype.require}", groups = DefaultGroup.class)
    @Pattern(regexp = "^(string|number|boolean|array|json)$", message = "{sysparams.valuetype.pattern}", groups = DefaultGroup.class)
    private String valueType;

    @Schema(description = "Notes")
    private String remark;

    @Schema(description = "Creation time")
    @JsonProperty(access = JsonProperty.Access.READ_ONLY)
    @JsonFormat(pattern = DateUtils.DATE_TIME_PATTERN)
    private Date createDate;

    @Schema(description = "Update time")
    @JsonProperty(access = JsonProperty.Access.READ_ONLY)
    @JsonFormat(pattern = DateUtils.DATE_TIME_PATTERN)
    private Date updateDate;

}
