package tbot.modules.model.dto;

import java.io.Serial;
import java.io.Serializable;

import cn.hutool.json.JSONObject;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "Model provider/vendor")
public class ModelConfigDTO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    @Schema(description = "Primary key")
    private String id;

    @Schema(description = "Model type (Memory/ASR/VAD/LLM/TTS)")
    private String modelType;

    @Schema(description = "Model code (such as AliLLM, DoubaoTTS)")
    private String modelCode;

    @Schema(description = "Model name")
    private String modelName;

    @Schema(description = "Default config? (0 No, 1 Yes)")
    private Integer isDefault;

    @Schema(description = "Enabled")
    private Integer isEnabled;

    @Schema(description = "Model config (JSON format)")
    private JSONObject configJson;

    @Schema(description = "Official documentation link")
    private String docLink;

    @Schema(description = "Notes")
    private String remark;

    @Schema(description = "Sort")
    private Integer sort;
}
