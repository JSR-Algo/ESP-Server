package tbot.modules.agent.service;

import java.util.List;
import java.util.Map;

import com.baomidou.mybatisplus.extension.service.IService;

import tbot.common.page.PageData;
import tbot.modules.agent.dto.AgentChatHistoryDTO;
import tbot.modules.agent.dto.AgentChatSessionDTO;
import tbot.modules.agent.entity.AgentChatHistoryEntity;
import tbot.modules.agent.vo.AgentChatHistoryUserVO;

/**
 * Agent chat record table handlingservice
 *
 * @author Goody
 * @version 1.0, 2025/4/30
 * @since 1.0.0
 */
public interface AgentChatHistoryService extends IService<AgentChatHistoryEntity> {

    /**
     * By agentIDGet session list
     *
     * @param params Query params, containsagentId,page,limit
     * @return Paged session list
     */
    PageData<AgentChatSessionDTO> getSessionListByAgentId(Map<String, Object> params);

    /**
     * According to SessionIDGet chat history list
     *
     * @param agentId   AgentID
     * @param sessionId SessionID
     * @return Chat record list
     */
    List<AgentChatHistoryDTO> getChatHistoryBySessionId(String agentId, String sessionId);

    /**
     * By agentIDDelete chat records
     *
     * @param agentId     AgentID
     * @param deleteAudio Delete audio?
     * @param deleteText  Delete text?
     */
    void deleteByAgentId(String agentId, Boolean deleteAudio, Boolean deleteText);

    /**
     * By agentIDGet Recent50user chat history records (with audio data)
     *
     * @param agentId Agentid
     * @return Chat record list (users only)
     */
    List<AgentChatHistoryUserVO> getRecentlyFiftyByAgentId(String agentId);

    /**
     * Based on audio dataIDGet chat content
     *
     * @param audioId Audioid
     * @return Chat Content
     */
    String getContentByAudioId(String audioId);


    /**
     * Query this audioidWhether belongs to this agent
     *
     * @param audioId Audioid
     * @param agentId Audioid
     * @return T: belongs to F: not belong to
     */
    boolean isAudioOwnedByAgent(String audioId,String agentId);
}
