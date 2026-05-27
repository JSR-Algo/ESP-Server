package tbot.modules.agent.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import lombok.Data;

/**
 * Agent voiceprint table
 *
 * @author zjy
 */
@TableName(value = "ai_agent_voice_print")
@Data
public class AgentVoicePrintEntity {
    /**
     * Primary keyid
     */
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;
    /**
     * Associated agentid
     */
    private String agentId;
    /**
     * Associated audioid
     */
    private String audioId;
    /**
     * Person name of voiceprint source
     */
    private String sourceName;
    /**
     * Describe person of voiceprint source
     */
    private String introduce;

    /**
     * Creator
     */
    @TableField(fill = FieldFill.INSERT)
    private Long creator;
    /**
     * Create time
     */
    @TableField(fill = FieldFill.INSERT)
    private Date createDate;

    /**
     * Updater
     */
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private Long updater;
    /**
     * Update time
     */
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private Date updateDate;
}
