package tbot.modules.agent.dao;

import java.util.List;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;

import tbot.modules.agent.entity.AgentChatHistoryEntity;

/**
 * {@link AgentChatHistoryEntity} Agent chat history recordDaoObject
 *
 * @author Goody
 * @version 1.0, 2025/4/30
 * @since 1.0.0
 */
@Mapper
public interface AiAgentChatHistoryDao extends BaseMapper<AgentChatHistoryEntity> {

    /**
     * By agentIDDelete chat history
     *
     * @param agentId AgentID
     */
    void deleteHistoryByAgentId(String agentId);

    /**
     * By agentIDDelete AudioID
     *
     * @param agentId AgentID
     */
    void deleteAudioIdByAgentId(String agentId);

    /**
     * By agentIDGet all audioIDList
     *
     * @param agentId AgentID
     * @return AudioIDList
     */
    List<String> getAudioIdsByAgentId(String agentId);

    /**
     * Batch delete audio
     *
     * @param audioIds AudioIDList
     */
    void deleteAudioByIds(@Param("audioIds") List<String> audioIds);
}
