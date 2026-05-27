package tbot.modules.agent.service.biz;

import tbot.modules.agent.dto.AgentChatHistoryReportDTO;

/**
 * Agent chat history business logic layer
 *
 * @author Goody
 * @version 1.0, 2025/4/30
 * @since 1.0.0
 */
public interface AgentChatHistoryBizService {

    /**
     * Chat report method
     *
     * @param agentChatHistoryReportDTO Input object containing info needed for chat report
     *                                  Example: deviceMACaddress, file type, content, etc.
     * @return Upload result,trueIndicates success,falseIndicate Failure
     */
    Boolean report(AgentChatHistoryReportDTO agentChatHistoryReportDTO);
}
