package tbot.modules.correctword.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@TableName("ai_agent_correct_word_item")
@Schema(description = "Replacement word entry")
public class CorrectWordItemEntity {

    @TableId(type = IdType.ASSIGN_UUID)
    @Schema(description = "Entry ID")
    private String id;

    @Schema(description = "Owner file ID")
    private String fileId;

    @Schema(description = "Original word")
    private String sourceWord;

    @Schema(description = "Replacement word")
    private String targetWord;
}
