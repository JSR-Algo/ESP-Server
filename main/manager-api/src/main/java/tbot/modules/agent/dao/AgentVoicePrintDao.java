package tbot.modules.agent.dao;

import org.apache.ibatis.annotations.Mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;

import tbot.modules.agent.entity.AgentChatHistoryEntity;
import tbot.modules.agent.entity.AgentVoicePrintEntity;

/**
 * {@link AgentChatHistoryEntity} Agent chat history recordDaoObject
 *
 * @author Goody
 * @version 1.0, 2025/4/30
 * @since 1.0.0
 */
@Mapper
public interface AgentVoicePrintDao extends BaseMapper<AgentVoicePrintEntity> {

}
