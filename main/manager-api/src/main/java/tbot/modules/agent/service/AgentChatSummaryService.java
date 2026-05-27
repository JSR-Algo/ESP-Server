package tbot.modules.agent.service;

/**
 * Agent chat record summary service interface
 */
public interface AgentChatSummaryService {

    /**
     * According to SessionIDGenerate chat history summary and save to agent memory
     * 
     * @param sessionId SessionID
     * @return Save Result
     */
    boolean generateAndSaveChatSummary(String sessionId);

    /**
     * According to SessionIDGenerate chat title and save
     *
     * @param sessionId SessionID
     * @return Successful
     */
    boolean generateAndSaveChatTitle(String sessionId);
}