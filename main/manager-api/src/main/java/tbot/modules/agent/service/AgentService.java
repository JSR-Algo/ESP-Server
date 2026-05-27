package tbot.modules.agent.service;

import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.agent.dto.AgentCreateDTO;
import tbot.modules.agent.dto.AgentDTO;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.vo.AgentInfoVO;

/**
 * Agent table handlingservice
 *
 * @author Goody
 * @version 1.0, 2025/4/30
 * @since 1.0.0
 */
public interface AgentService extends BaseService<AgentEntity> {
    /**
     * Get admin agent list
     *
     * @param params Query parameters
     * @return Paged Data
     */
    PageData<AgentEntity> adminAgentList(Map<String, Object> params);

    /**
     * Based onIDGet agent
     *
     * @param id AgentID
     * @return Agent entity
     */
    AgentInfoVO getAgentById(String id);

    /**
     * Insert agent
     *
     * @param entity Agent entity
     * @return Successful
     */
    boolean insert(AgentEntity entity);

    /**
     * According to UserIDDelete agent
     *
     * @param userId UserID
     */
    void deleteAgentByUserId(Long userId);

    /**
     * Get user agent list
     *
     * @param userId UserID
     * @param keyword Search keyword
     * @param searchType Search type (name - Search by name,mac - byMACAddress search)
     * @return Agent list
     */
    List<AgentDTO> getUserAgents(Long userId, String keyword, String searchType);

    /**
     * By agentIDGet device count
     *
     * @param agentId AgentID
     * @return Device count
     */
    Integer getDeviceCountByAgentId(String agentId);

    /**
     * According to DeviceMACQuery default agent info of corresponding device by address
     *
     * @param macAddress DeviceMACAddress
     * @return Default agent info, return when not existsnull
     */
    AgentEntity getDefaultAgentByMacAddress(String macAddress);

    /**
     * Check whether user has permission to access agent
     *
     * @param agentId AgentID
     * @param userId  UserID
     * @return Has permission
     */
    boolean checkAgentPermission(String agentId, Long userId);

    /**
     * Update agent
     *
     * @param agentId AgentID
     * @param dto     Information required to update agent
     */
    void updateAgentById(String agentId, AgentUpdateDTO dto);

    /**
     * Create agent
     *
     * @param dto Information required to create agent
     * @return Created agentID
     */
    String createAgent(AgentCreateDTO dto);


}
