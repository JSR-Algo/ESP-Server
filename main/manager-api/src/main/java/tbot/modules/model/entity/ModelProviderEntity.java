package tbot.modules.model.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@TableName("ai_model_provider")
@Schema(description = "Model provider table")
public class ModelProviderEntity {

    @TableId(type = IdType.ASSIGN_UUID)
    @Schema(description = "Primary key")
    private String id;

    @Schema(description = "Model type (Memory/ASR/VAD/LLM/TTS)")
    private String modelType;

    @Schema(description = "Provider type, such as openai,")
    private String providerCode;

    @Schema(description = "Provider name")
    private String name;

    @Schema(description = "Provider field list (JSON format)")
    private String fields;

    @Schema(description = "Sort")
    private Integer sort;

    @Schema(description = "Creator")
    private Long creator;

    @Schema(description = "Creation time")
    private Date createDate;

    @Schema(description = "Updater")
    private Long updater;

    @Schema(description = "Update time")
    private Date updateDate;
}
