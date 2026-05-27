package tbot.modules.llm.service;

/**
 * LLMService API
 * Support multiple large model calls
 */
public interface LLMService {

    /**
     * Generate chat history summary
     * 
     * @param conversation   Dialogue Content
     * @param promptTemplate Prompt template
     * @return Summary Result
     */
    String generateSummary(String conversation, String promptTemplate);

    /**
     * Generate chat history summary (use default prompt)
     * 
     * @param conversation Dialogue Content
     * @return Summary Result
     */
    String generateSummary(String conversation);

    /**
     * Generate chat record summary (specified modelID)
     * 
     * @param conversation Dialogue Content
     * @param modelId      ModelID
     * @return Summary Result
     */
    String generateSummaryWithModel(String conversation, String modelId);

    /**
     * Generate chat record summary (specified modelIDAnd prompt template)
     * 
     * @param conversation   Dialogue Content
     * @param promptTemplate Prompt template
     * @param modelId        ModelID
     * @return Summary Result
     */
    String generateSummary(String conversation, String promptTemplate, String modelId);

    /**
     * Generate chat history summary (including historical memory merge)
     * 
     * @param conversation   Dialogue Content
     * @param historyMemory  Historical Memory
     * @param promptTemplate Prompt template
     * @param modelId        ModelID
     * @return Summary Result
     */
    String generateSummaryWithHistory(String conversation, String historyMemory, String promptTemplate, String modelId);

    /**
     * Check service availability
     * 
     * @return Available
     */
    boolean isAvailable();

    /**
     * Check whether specified model service available
     * 
     * @param modelId ModelID
     * @return Available
     */
    boolean isAvailable(String modelId);

    /**
     * Generate session title
     * 
     * @param conversation Dialogue Content
     * @param modelId      ModelID
     * @return Title (about15character)
     */
    String generateTitle(String conversation, String modelId);
}