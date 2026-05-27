package tbot.modules.agent.dao;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import org.apache.ibatis.annotations.Select;
import tbot.common.dao.BaseDao;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.vo.AgentInfoVO;

@Mapper
public interface AgentDao extends BaseDao<AgentEntity> {
    /**
     * Get agent device count
     * 
     * @param agentId AgentID
     * @return Device count
     */
    Integer getDeviceCountByAgentId(@Param("agentId") String agentId);

    /**
     * According to DeviceMACQuery default agent info of corresponding device by address
     *
     * @param macAddress DeviceMACAddress
     * @return Default agent info
     */
    @Select(" SELECT a.* FROM ai_device d " +
            " LEFT JOIN ai_agent a ON d.agent_id = a.id " +
            " WHERE d.mac_address = #{macAddress} " +
            " ORDER BY d.id DESC LIMIT 1")
    AgentEntity getDefaultAgentByMacAddress(@Param("macAddress") String macAddress);

    /**
     * Based onidQueryagentInfo, including plugin info
     *
     * @param agentId AgentID
     */
    AgentInfoVO selectAgentInfoById(@Param("agentId") String agentId);
}
