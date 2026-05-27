package tbot.modules.agent.service;

import java.util.List;

import com.baomidou.mybatisplus.extension.service.IService;

import tbot.modules.agent.entity.AgentPluginMapping;

/**
 * @description For Table [ai_agent_plugin_mapping(AgentUnique mapping table with plugin)Database operation for ]Service
 * @createDate 2025-05-25 22:33:17
 */
public interface AgentPluginMappingService extends IService<AgentPluginMapping> {

    /**
     * By agentidGet plugin parameters
     * 
     * @param agentId
     * @return
     */
    List<AgentPluginMapping> agentPluginParamsByAgentId(String agentId);

    /**
     * By agentidDelete plugin parameters
     * 
     * @param agentId
     */
    void deleteByAgentId(String agentId);
}
