package tbot.modules.agent.service;


import java.util.List;

/**
 * AgentMcpAccess point handlingservice
 *
 * @author zjy
 */
public interface AgentMcpAccessPointService {
    /**
     * Get agent'smcpAccess point address
     * @param id Agentid
     * @return mcpAccess point address
     */
   String getAgentMcpAccessAddress(String id);

    /**
     * Get agent'smcpExisting tool list of access point
     * @param id Agentid
     * @return Tool list
     */
   List<String> getAgentMcpToolsList(String id);
}
