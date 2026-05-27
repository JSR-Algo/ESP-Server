package tbot.modules.agent.dao;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import tbot.modules.agent.entity.AgentPluginMapping;
import com.baomidou.mybatisplus.core.mapper.BaseMapper;

import java.util.List;

/**
* @description For Table [ai_agent_plugin_mapping(AgentUnique mapping table with plugin)Database operation for ]Mapper
* @createDate 2025-05-25 22:33:17
* @Entity tbot.modules.agent.entity.AgentPluginMapping
*/
@Mapper
public interface AgentPluginMappingMapper extends BaseMapper<AgentPluginMapping> {
    List<AgentPluginMapping> selectPluginsByAgentId(@Param("agentId") String agentId);
}




