package tbot.modules.correctword.vo;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "Replacement word compact VO (for device side)")
public class CorrectWordSimpleVO {

    @Schema(description = "Original word")
    private String sourceWord;

    @Schema(description = "Replacement word")
    private String targetWord;
}
