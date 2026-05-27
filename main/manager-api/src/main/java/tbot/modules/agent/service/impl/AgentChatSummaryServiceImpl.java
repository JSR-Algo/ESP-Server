package tbot.modules.agent.service.impl;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.apache.commons.lang3.StringUtils;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;

import lombok.RequiredArgsConstructor;
import tbot.common.constant.Constant;
import tbot.modules.agent.dto.AgentChatHistoryDTO;
import tbot.modules.agent.dto.AgentChatSummaryDTO;
import tbot.modules.agent.dto.AgentMemoryDTO;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.entity.AgentChatHistoryEntity;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentChatSummaryService;
import tbot.modules.agent.service.AgentChatTitleService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.vo.AgentInfoVO;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;
import tbot.modules.llm.service.LLMService;
import tbot.modules.model.entity.ModelConfigEntity;
import tbot.modules.model.service.ModelConfigService;

/**
 * Agent chat recordsSummary service implementation class
 * ImplementPythonendmem_local_short.pysummary logic in
 */
@Service
@RequiredArgsConstructor
public class AgentChatSummaryServiceImpl implements AgentChatSummaryService {

    private static final Logger log = LoggerFactory.getLogger(AgentChatSummaryServiceImpl.class);

    private final AgentChatHistoryService agentChatHistoryService;
    private final AgentService agentService;
    private final AgentChatTitleService agentChatTitleService;
    private final DeviceService deviceService;
    private final LLMService llmService;
    private final ModelConfigService modelConfigService;

    // Summary rule constant
    private static final int MAX_SUMMARY_LENGTH = 1800; // Max summary length
    private static final Pattern JSON_PATTERN = Pattern.compile("\\{.*?\\}", Pattern.DOTALL);
    private static final Pattern DEVICE_CONTROL_PATTERN = Pattern.compile("device control|device operation|control device|device status",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern WEATHER_PATTERN = Pattern.compile("weather|temperature|humidity|rainfall|meteorology", Pattern.CASE_INSENSITIVE);
    private static final Pattern DATE_PATTERN = Pattern.compile("date|time|weekday|month|year", Pattern.CASE_INSENSITIVE);

    private AgentChatSummaryDTO generateChatSummary(String sessionId) {
        try {
            System.out.println("Start generating session " + sessionId + " chat record summary");

            // 1. Based onsessionIdGet chat records
            List<AgentChatHistoryDTO> chatHistory = getChatHistoryBySessionId(sessionId);
            if (chatHistory == null || chatHistory.isEmpty()) {
                return new AgentChatSummaryDTO(sessionId, "Chat history for this session not found");
            }

            // 2. GetAgent info
            String agentId = getAgentIdFromSession(sessionId, chatHistory);
            if (StringUtils.isBlank(agentId)) {
                return new AgentChatSummaryDTO(sessionId, "Cannot get agent info");
            }

            // 3. Extract key conversationsContent
            List<String> meaningfulMessages = extractMeaningfulMessages(chatHistory);
            if (meaningfulMessages.isEmpty()) {
                return new AgentChatSummaryDTO(sessionId, "No valid conversation content to summarize");
            }

            // 4. Generate summary (generateSummaryFromMessagesmethod already contains length limit logic)
            String summary = generateSummaryFromMessages(meaningfulMessages, agentId);

            log.info("Successfully generated chat history summary for session {}, length: {} chars", sessionId, summary.length());
            return new AgentChatSummaryDTO(sessionId, agentId, summary);

        } catch (Exception e) {
            log.error("Error generating chat history summary for session {}: {}", sessionId, e.getMessage());
            return new AgentChatSummaryDTO(sessionId, "Error generating summary: " + e.getMessage());
        }
    }

    @Override
    public boolean generateAndSaveChatSummary(String sessionId) {
        try {
            DeviceEntity device = getDeviceBySessionId(sessionId);
            if (device == null) {
                log.info("No device found associated with session {}", sessionId);
                return false;
            }

            String agentId = device.getAgentId();
            String memModelId = agentService.getAgentById(agentId).getMemModelId();

            if (memModelId == null || memModelId.equals(Constant.MEMORY_MEM_REPORT_ONLY)) {
                log.info("Session {} uses chat-history-only reporting mode, skip memory summary", sessionId);
                return true;
            }

            boolean shouldSummarizeMemory = !memModelId.equals(Constant.MEMORY_NO_MEM)
                    && !memModelId.equals(Constant.MEMORY_MEM0AI)
                    && !memModelId.equals(Constant.MEMORY_POWERMEM);

            if (shouldSummarizeMemory) {
                AgentChatSummaryDTO summaryDTO = generateChatSummary(sessionId);
                if (summaryDTO.isSuccess()) {
                    agentService.updateAgentById(agentId, new AgentUpdateDTO() {
                        {
                            setSummaryMemory(summaryDTO.getSummary());
                        }
                    });
                    log.info("Successfully saved chat history summary for session {} to agent {}", sessionId, agentId);
                } else {
                    log.info("Failed to generate summary: {}", summaryDTO.getErrorMessage());
                }
            } else {
                log.info("Session {} uses {} mode, skip memory summary", sessionId, memModelId);
            }

            return true;

        } catch (Exception e) {
            log.error("Error saving chat history summary for session {}: {}", sessionId, e.getMessage());
            return false;
        }
    }

    @Override
    public boolean generateAndSaveChatTitle(String sessionId) {
        try {
            // Auto GetagentId
            String agentId = findAgentIdBySessionId(sessionId);
            if (StringUtils.isBlank(agentId)) {
                log.warn("Session {} cannot get agent info, skip title generation", sessionId);
                return false;
            }

            List<AgentChatHistoryDTO> chatHistory = getChatHistoryBySessionId(sessionId);
            if (chatHistory == null || chatHistory.isEmpty()) {
                return false;
            }

            List<String> meaningfulMessages = extractMeaningfulMessages(chatHistory);
            if (meaningfulMessages.isEmpty()) {
                return false;
            }

            StringBuilder conversation = new StringBuilder();
            for (int i = 0; i < meaningfulMessages.size(); i++) {
                conversation.append("Message").append(i + 1).append(": ").append(meaningfulMessages.get(i)).append("\n");
            }

            String slmModelId = getSlmModelId(agentId);
            String title = llmService.generateTitle(conversation.toString(), slmModelId);

            if (StringUtils.isNotBlank(title)) {
                agentChatTitleService.saveOrUpdateTitle(sessionId, title);
                log.info("Saved title for session {}: {}", sessionId, title);
                return true;
            }
            return false;
        } catch (Exception e) {
            log.error("Error generating title for session {}: {}", sessionId, e.getMessage());
            return false;
        }
    }

    private String getSlmModelId(String agentId) {
        try {
            if (StringUtils.isBlank(agentId)) {
                return null;
            }

            AgentInfoVO agentInfo = agentService.getAgentById(agentId);
            if (agentInfo == null) {
                return null;
            }

            String slmModelId = agentInfo.getSlmModelId();
            if (StringUtils.isNotBlank(slmModelId)) {
                log.info("Session {} uses SLM model: {}", agentId, slmModelId);
                return slmModelId;
            }

            ModelConfigEntity defaultLlmConfig = getDefaultLLMConfig();
            if (defaultLlmConfig != null) {
                log.info("Session {} uses default LLM model: {}", agentId, defaultLlmConfig.getId());
                return defaultLlmConfig.getId();
            }

            String llmModelId = agentInfo.getLlmModelId();
            log.info("Session {} uses LLM model (final fallback): {}", agentId, llmModelId);
            return llmModelId;
        } catch (Exception e) {
            log.error("Failed to get agent slm model ID, agentId: {}, error: {}", agentId, e.getMessage());
            return null;
        }
    }

    private ModelConfigEntity getDefaultLLMConfig() {
        try {
            List<ModelConfigEntity> llmConfigs = modelConfigService.getEnabledModelsByType("LLM");
            if (llmConfigs == null || llmConfigs.isEmpty()) {
                return null;
            }

            for (ModelConfigEntity config : llmConfigs) {
                if (config.getIsDefault() != null && config.getIsDefault() == 1) {
                    return config;
                }
            }

            return llmConfigs.get(0);
        } catch (Exception e) {
            log.error("Get default LLM config failed: {}", e.getMessage());
            return null;
        }
    }

    /**
     * Based onSession IDGet chat records
     */
    private List<AgentChatHistoryDTO> getChatHistoryBySessionId(String sessionId) {
        try {
            // Here need based onsessionIdGet chat records
            // Existing API needsagentId, we need first find relatedagentId
            String agentId = findAgentIdBySessionId(sessionId);
            if (StringUtils.isBlank(agentId)) {
                return null;
            }
            return agentChatHistoryService.getChatHistoryBySessionId(agentId, sessionId);
        } catch (Exception e) {
            log.error("Failed to get chat history for session {}: {}", sessionId, e.getMessage());
            return null;
        }
    }

    /**
     * Based onSession IDFind associatedAgent ID
     */
    private String findAgentIdBySessionId(String sessionId) {
        try {
            // Query first record of session to getagentId
            QueryWrapper<AgentChatHistoryEntity> wrapper = new QueryWrapper<>();
            wrapper.select("agent_id")
                    .eq("session_id", sessionId)
                    .last("LIMIT 1");

            AgentChatHistoryEntity entity = agentChatHistoryService.getOne(wrapper);
            return entity != null ? entity.getAgentId() : null;
        } catch (Exception e) {
            log.error("Failed to find agent ID by session ID {}: {}", sessionId, e.getMessage());
            return null;
        }
    }

    /**
     * Get from sessionAgent ID
     */
    private String getAgentIdFromSession(String sessionId, List<AgentChatHistoryDTO> chatHistory) {
        // Query directly from databaseAgent ID
        return findAgentIdBySessionId(sessionId);
    }

    /**
     * Extract meaningful dialogueContent(extract only userMessage, excludeAIReply)
     */
    private List<String> extractMeaningfulMessages(List<AgentChatHistoryDTO> chatHistory) {
        List<String> meaningfulMessages = new ArrayList<>();

        for (AgentChatHistoryDTO message : chatHistory) {
            // Handle user onlyMessage(chatType = 1)
            if (message.getChatType() != null && message.getChatType() == 1) {
                String content = extractContentFromMessage(message);
                if (isMeaningfulMessage(content)) {
                    meaningfulMessages.add(content);
                }
            }
        }

        return meaningfulMessages;
    }

    /**
     * fromMessageExtract fromContent(ProcessJSONFormat)
     */
    private String extractContentFromMessage(AgentChatHistoryDTO message) {
        String content = message.getContent();
        if (StringUtils.isBlank(content)) {
            return "";
        }

        // ProcessJSONFormatContent(with FrontendChatHistoryDialog.vueLogic consistent)
        Matcher matcher = JSON_PATTERN.matcher(content);
        if (matcher.find()) {
            String jsonContent = matcher.group();
            // Simplified handling: extractJSONtext inContent
            return extractTextFromJson(jsonContent);
        }

        return content;
    }

    /**
     * fromJSONExtract text fromContent
     */
    private String extractTextFromJson(String jsonContent) {
        // Simplified handling: extract"content"field value
        Pattern contentPattern = Pattern.compile("\"content\"\s*:\s*\"([^\"]*)\"");
        Matcher matcher = contentPattern.matcher(jsonContent);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return jsonContent;
    }

    /**
     * Determine if meaningfulMessage
     */
    private boolean isMeaningfulMessage(String content) {
        if (StringUtils.isBlank(content)) {
            return false;
        }

        // Exclude device controlInfo
        if (DEVICE_CONTROL_PATTERN.matcher(content).find()) {
            return false;
        }

        // Exclude irrelevant date weather etc.Content
        if (WEATHER_PATTERN.matcher(content).find() || DATE_PATTERN.matcher(content).find()) {
            return false;
        }

        // Exclude too shortMessage
        return content.length() >= 5;
    }

    /**
     * fromMessageGenerate Summary
     */
    private String generateSummaryFromMessages(List<String> messages, String agentId) {
        if (messages.isEmpty()) {
            return "This conversation has little content, no important info needs summary.";
        }

        // Build complete conversationContent
        StringBuilder conversation = new StringBuilder();
        for (int i = 0; i < messages.size(); i++) {
            conversation.append("Message").append(i + 1).append(": ").append(messages.get(i)).append("\n");
        }

        try {
            // Get current agent history memory
            String historyMemory = getCurrentAgentMemory(agentId);

            // CallLLMservice performs intelligent summary, passagentIdto get correctModel config
            String summary = callJavaLLMForSummaryWithHistory(conversation.toString(), historyMemory, agentId);

            // Apply summary rules: limit max length
            if (summary.length() > MAX_SUMMARY_LENGTH) {
                summary = summary.substring(0, MAX_SUMMARY_LENGTH) + "...";
            }

            return summary;
        } catch (Exception e) {
            log.error("Failed to call Java LLM service: {}", e.getMessage());
            throw new RuntimeException("LLM service unavailable, cannot generate chat summary");
        }
    }

    /**
     * Get current agent history memory
     */
    private String getCurrentAgentMemory(String agentId) {
        try {
            if (StringUtils.isBlank(agentId)) {
                return null;
            }

            // GetAgent info
            AgentInfoVO agentInfo = agentService.getAgentById(agentId);
            if (agentInfo == null) {
                return null;
            }

            // Return agent currentSummary memory
            return agentInfo.getSummaryMemory();
        } catch (Exception e) {
            log.error("Failed to get agent history memory, agentId: {}, error: {}", agentId, e.getMessage());
            return null;
        }
    }

    /**
     * CallJavaendLLMService performs intelligent summary (supports historical memory merge)
     */
    private String callJavaLLMForSummaryWithHistory(String conversation, String historyMemory, String agentId) {
        try {
            String modelId = getSlmModelId(agentId);

            if (StringUtils.isBlank(modelId)) {
                log.info("SLM model not found, using default LLM service");
                return llmService.generateSummaryWithHistory(conversation, historyMemory, null, null);
            }

            String summary = llmService.generateSummaryWithHistory(conversation, historyMemory, null, modelId);

            if (StringUtils.isNotBlank(summary) && !summary.equals("Service unavailable") && !summary.equals("Summary generation failed")) {
                return summary;
            }

            throw new RuntimeException("Java-side LLM service returned exception: " + summary);

        } catch (Exception e) {
            log.error("Exception calling Java LLM service, agentId: {}, error: {}", agentId, e.getMessage());
            throw e;
        }
    }

    /**
     * CallJavaendLLMService performs smart summary
     */
    private String callJavaLLMForSummary(String conversation, String agentId) {
        try {
            String modelId = getSlmModelId(agentId);

            if (StringUtils.isBlank(modelId)) {
                log.info("SLM model not found, using default LLM service");
                return llmService.generateSummary(conversation);
            }

            String summary = llmService.generateSummaryWithModel(conversation, modelId);

            if (StringUtils.isNotBlank(summary) && !summary.equals("Service unavailable") && !summary.equals("Summary generation failed")) {
                return summary;
            }

            throw new RuntimeException("Java-side LLM service returned exception: " + summary);

        } catch (Exception e) {
            log.error("Exception calling Java LLM service, agentId: {}, error: {}", agentId, e.getMessage());
            throw e;
        }
    }

    /**
     * Get memory summaryLLMModel ID
     */
    private String getMemorySummaryModelId(String agentId) {
        try {
            if (StringUtils.isBlank(agentId)) {
                return null;
            }

            // GetAgent info
            AgentInfoVO agentInfo = agentService.getAgentById(agentId);
            if (agentInfo == null) {
                return null;
            }

            // Get agent'sMemory model ID
            String memModelId = agentInfo.getMemModelId();
            if (StringUtils.isBlank(memModelId)) {
                return null;
            }

            // Get MemoryModel config
            ModelConfigEntity memModelConfig = modelConfigService.getModelByIdFromCache(memModelId);
            if (memModelConfig == null || memModelConfig.getConfigJson() == null) {
                return null;
            }

            // From memoryModel configExtract corresponding fromLLMModel ID
            Map<String, Object> configMap = memModelConfig.getConfigJson();
            String llmModelId = (String) configMap.get("llm");

            if (StringUtils.isBlank(llmModelId)) {
                // If memory model has no independent configLLMthen use agent defaultLLMModel
                return agentInfo.getLlmModelId();
            }

            return llmModelId;
        } catch (Exception e) {
            log.error("Failed to get memory summary LLM model ID, agentId: {}, error: {}", agentId, e.getMessage());
            return null;
        }
    }

    /**
     * Based onSession IDGetDevice info
     */
    private DeviceEntity getDeviceBySessionId(String sessionId) {
        try {
            // Query first record of session to getmacAddress
            QueryWrapper<AgentChatHistoryEntity> wrapper = new QueryWrapper<>();
            wrapper.select("mac_address")
                    .eq("session_id", sessionId)
                    .last("LIMIT 1");

            AgentChatHistoryEntity entity = agentChatHistoryService.getOne(wrapper);
            if (entity != null && StringUtils.isNotBlank(entity.getMacAddress())) {
                return deviceService.getDeviceByMacAddress(entity.getMacAddress());
            }
            return null;
        } catch (Exception e) {
            log.error("Failed to find device info by session ID {}: {}", sessionId, e.getMessage());
            return null;
        }
    }
}