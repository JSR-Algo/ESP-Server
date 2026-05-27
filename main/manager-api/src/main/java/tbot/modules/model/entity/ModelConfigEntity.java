package tbot.modules.model.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;

import cn.hutool.json.JSONObject;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@TableName(value = "ai_model_config", autoResultMap = true)
@Schema(description = "Model config table")
public class ModelConfigEntity {

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

    @TableField(typeHandler = JacksonTypeHandler.class)
    @Schema(description = "Model config (JSON format)")
    private JSONObject configJson;

    @Schema(description = "Official documentation link")
    private String docLink;

    @Schema(description = "Notes")
    private String remark;

    @Schema(description = "Sort")
    private Integer sort;

    @Schema(description = "Updater")
    @TableField(fill = FieldFill.UPDATE)
    private Long updater;

    @Schema(description = "Update time")
    @TableField(fill = FieldFill.UPDATE)
    private Date updateDate;

    @Schema(description = "Creator")
    @TableField(fill = FieldFill.INSERT)
    private Long creator;

    @Schema(description = "Creation time")
    @TableField(fill = FieldFill.INSERT)
    private Date createDate;
}
