package tbot.modules.agent.service;

import tbot.common.service.BaseService;
import tbot.modules.agent.entity.AgentContextProviderEntity;

public interface AgentContextProviderService extends BaseService<AgentContextProviderEntity> {
    /**
     * By agentIDGet context source config
     * @param agentId AgentID
     * @return Context source config entity
     */
    AgentContextProviderEntity getByAgentId(String agentId);

    /**
     * Save or update context source config
     * @param entity Entity
     */
    void saveOrUpdateByAgentId(AgentContextProviderEntity entity);

    /**
     * By agentIDDelete context source config
     * @param agentId AgentID
     */
    void deleteByAgentId(String agentId);
}
