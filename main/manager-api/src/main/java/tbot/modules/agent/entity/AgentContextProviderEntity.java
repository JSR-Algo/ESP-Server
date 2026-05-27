package tbot.modules.agent.entity;

import java.util.Date;
import java.util.List;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;
import tbot.modules.agent.dto.ContextProviderDTO;

@Data
@TableName(value = "ai_agent_context_provider", autoResultMap = true)
@Schema(description = "Agent context source config")
public class AgentContextProviderEntity {

    @TableId(type = IdType.ASSIGN_UUID)
    @Schema(description = "Primary key")
    private String id;

    @Schema(description = "Agent ID")
    private String agentId;

    @Schema(description = "Context source config")
    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<ContextProviderDTO> contextProviders;

    @Schema(description = "Creator")
    private Long creator;

    @Schema(description = "Creation time")
    private Date createdAt;

    @Schema(description = "Updater")
    private Long updater;

    @Schema(description = "Update time")
    private Date updatedAt;
}
