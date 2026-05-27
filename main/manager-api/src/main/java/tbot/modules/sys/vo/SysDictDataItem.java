package tbot.modules.sys.vo;

import java.io.Serializable;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Dictionary data VO
 */
@Data
@Schema(description = "Dictionary data item")
public class SysDictDataItem implements Serializable {

    @Schema(description = "Dictionary tag")
    private String name;

    @Schema(description = "Dictionary value")
    private String key;
}
