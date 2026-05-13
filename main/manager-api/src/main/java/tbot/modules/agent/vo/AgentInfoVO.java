package tbot.modules.agent.vo;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;
import lombok.EqualsAndHashCode;
import tbot.modules.agent.dto.ContextProviderDTO;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.entity.AgentPluginMapping;

import java.util.List;

/**
 * AgentInfoResponse bodyVO
 * Here DirectlyextendcompletedAgentEntity classAgentEntity, subsequent need standardize returned fields cancopyField Out
 */
@EqualsAndHashCode(callSuper = true)
@Data
public class AgentInfoVO extends AgentEntity
{
    @Schema(description = "Plugin list ID")
    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<AgentPluginMapping> functions;

    @Schema(description = "Context source config")
    private List<ContextProviderDTO> contextProviders;

    @Schema(description = "Replacement word file ID list")
    private List<String> correctWordFileIds;
}
