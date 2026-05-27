package tbot.modules.agent.dao;

import org.apache.ibatis.annotations.Mapper;
import tbot.common.dao.BaseDao;
import tbot.modules.agent.entity.AgentContextProviderEntity;

@Mapper
public interface AgentContextProviderDao extends BaseDao<AgentContextProviderEntity> {
}
