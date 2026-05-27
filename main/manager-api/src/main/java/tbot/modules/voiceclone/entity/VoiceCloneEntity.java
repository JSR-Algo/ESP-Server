package tbot.modules.voiceclone.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;
import lombok.EqualsAndHashCode;

@Data
@EqualsAndHashCode(callSuper = false)
@TableName("ai_voice_clone")
@Schema(description = "Voice cloning")
public class VoiceCloneEntity {

    @TableId(type = IdType.ASSIGN_UUID)
    @Schema(description = "Unique identifier")
    private String id;

    @Schema(description = "Voice name")
    private String name;

    @Schema(description = "Model id")
    private String modelId;

    @Schema(description = "Voice id")
    private String voiceId;

    @Schema(description = "Language")
    private String languages;

    @Schema(description = "User ID (linked user table)")
    private Long userId;

    @Schema(description = "Voice")
    private byte[] voice;

    @Schema(description = "Training status: 0 pending 1 training 2 success 3 failed")
    private Integer trainStatus;

    @Schema(description = "Training error reason")
    private String trainError;

    @Schema(description = "Creator")
    @TableField(fill = FieldFill.INSERT)
    private Long creator;

    @Schema(description = "Creation time")
    @TableField(fill = FieldFill.INSERT)
    private Date createDate;
}
