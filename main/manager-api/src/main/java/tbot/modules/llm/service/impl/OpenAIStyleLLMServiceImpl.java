package tbot.modules.llm.service.impl;

import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import cn.hutool.json.JSONArray;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import lombok.extern.slf4j.Slf4j;
import tbot.common.exception.RenException;
import tbot.modules.llm.service.LLMService;
import tbot.modules.model.entity.ModelConfigEntity;
import tbot.modules.model.service.ModelConfigService;

/**
 * OpenAIStyleAPIofLLMService Implementation
 * Support Alibaba Cloud,DeepSeek,ChatGLMAnd compatibleOpenAI APIModel of
 */
@Slf4j
@Service
public class OpenAIStyleLLMServiceImpl implements LLMService {

    // NeedDisableThinking mode platform domain and corresponding parameters
    private static final Map<String, Map<String, Object>> THINKING_DISABLED_DOMAINS;
    static {
        Map<String, Map<String, Object>> map = new LinkedHashMap<>();
        map.put("aliyuncs.com", Map.of("enable_thinking", false));
        Map<String, Object> thinkingDisabled = Map.of("thinking", Map.of("type", "disabled"));
        map.put("bigmodel.cn", thinkingDisabled);
        map.put("moonshot.cn", thinkingDisabled);
        map.put("volces.com", thinkingDisabled);
        THINKING_DISABLED_DOMAINS = Collections.unmodifiableMap(map);
    }

    @Autowired
    private ModelConfigService modelConfigService;

    @Autowired
    private RestTemplate restTemplate;

    /**
     * Auto-disable thinking mode by domain
     */
    private void applyThinkingDisabled(String baseUrl, Map<String, Object> requestBody) {
        for (Map.Entry<String, Map<String, Object>> entry : THINKING_DISABLED_DOMAINS.entrySet()) {
            if (baseUrl.contains(entry.getKey())) {
                requestBody.putAll(entry.getValue());
                log.info("Disable thinking mode for domain {}, parameters: {}", baseUrl, entry.getValue());
                break;
            }
        }
    }

    /**
     * Validate LLM response body for security and size
     */
    private void validateLlmResponse(String responseBody) {
        if (responseBody == null || responseBody.isEmpty()) {
            throw new RenException("LLM response is empty");
        }
        if (responseBody.length() > 100000) {
            throw new RenException("LLM response exceeds maximum size");
        }
    }

    private static final String DEFAULT_SUMMARY_PROMPT = "You are an experienced memory summarizer, good at summarizing conversations. Follow these rules:\n1. Summarize important user information to provide more personalized service in future conversations\n2. Do not summarize repeatedly. Do not forget previous memory unless original memory exceeds 1800 characters. Do not forget or compress user history memory\n3. Content unrelated to user, such as device volume control, music playback, weather, exit, not wanting to chat, does not need to be added to summary\n4. Data unrelated to user events, such as today's date/time and today's weather in chat content, will affect later conversations if stored as memory. Do not add this information to summary\n5. Do not add device control success/failure results to summary. Do not add user filler talk to summary\n6. Do not summarize just to summarize. If user chat has no meaning, returning original history is also OK\n7. Return summary only, strictly within 1800 characters\n8. Do not include code or xml. No explanations, comments, or notes needed. When saving memory, extract only from conversation. Do not mix in example content\n9. If history memory is provided, intelligently merge new conversation content with history memory, keeping valuable history information while adding new important information\n\nHistory memory:\n{history_memory}\n\nNew conversation content:\n{conversation}";

    private static final String DEFAULT_TITLE_PROMPT = "Based on following conversation, generate concise session title (within about 15 characters). Return only title, no explanation or punctuation:\n{conversation}";

    @Override
    public String generateSummary(String conversation) {
        return generateSummary(conversation, null, null);
    }

    @Override
    public String generateSummaryWithModel(String conversation, String modelId) {
        return generateSummary(conversation, null, modelId);
    }

    @Override
    public String generateSummary(String conversation, String promptTemplate, String modelId) {
        if (!isAvailable()) {
            log.warn("LLM service unavailable, cannot generate summary");
            return "LLM service unavailable, cannot generate summary";
        }

        try {
            // Get from Control PanelLLMModel config
            ModelConfigEntity llmConfig;
            if (modelId != null && !modelId.trim().isEmpty()) {
                // Through SpecificModel IDGet Config
                llmConfig = modelConfigService.getModelByIdFromCache(modelId);
            } else {
                // Keep backward compatibility, use default config
                llmConfig = getDefaultLLMConfig();
            }

            if (llmConfig == null || llmConfig.getConfigJson() == null) {
                log.error("No available LLM model config found, modelId: {}", modelId);
                return "No available LLM model config found";
            }

            JSONObject configJson = llmConfig.getConfigJson();
            String baseUrl = configJson.getStr("base_url");
            String model = configJson.getStr("model_name");
            String apiKey = configJson.getStr("api_key");
            Double temperature = configJson.getDouble("temperature");
            Integer maxTokens = configJson.getInt("max_tokens");

            if (StringUtils.isBlank(baseUrl) || StringUtils.isBlank(apiKey)) {
                log.error("LLM config incomplete, baseUrl or apiKey empty");
                return "LLM config incomplete, cannot generate summary";
            }

            // BuildPromptword
            String prompt = (promptTemplate != null ? promptTemplate : DEFAULT_SUMMARY_PROMPT).replace("{conversation}",
                    conversation);

            // Build request body
            Map<String, Object> requestBody = new HashMap<>();
            requestBody.put("model", model != null ? model : "gpt-3.5-turbo");

            Map<String, Object>[] messages = new Map[1];
            Map<String, Object> message = new HashMap<>();
            message.put("role", "user");
            message.put("content", prompt);
            messages[0] = message;

            requestBody.put("messages", messages);
            requestBody.put("temperature", temperature != null ? temperature : 0.7);
            requestBody.put("max_tokens", maxTokens != null ? maxTokens : 2000);

            // DisableThinking Mode
            applyThinkingDisabled(baseUrl, requestBody);

            // SendHTTPRequest
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("Authorization", "Bearer " + apiKey);

            HttpEntity<Map<String, Object>> entity = new HttpEntity<>(requestBody, headers);

            // Build completeAPI URL
            String apiUrl = baseUrl;
            if (!apiUrl.endsWith("/chat/completions")) {
                if (!apiUrl.endsWith("/")) {
                    apiUrl += "/";
                }
                apiUrl += "chat/completions";
            }

            ResponseEntity<String> response = restTemplate.exchange(
                    apiUrl, HttpMethod.POST, entity, String.class);

            if (response.getStatusCode().is2xxSuccessful()) {
                validateLlmResponse(response.getBody());
                JSONObject responseJson = JSONUtil.parseObj(response.getBody());
                JSONArray choices = responseJson.getJSONArray("choices");
                if (choices != null && choices.size() > 0) {
                    JSONObject choice = choices.getJSONObject(0);
                    JSONObject messageObj = choice.getJSONObject("message");
                    return messageObj.getStr("content");
                }
            } else {
                log.error("LLM API call failed, status code: {}, response: {}", response.getStatusCode(), response.getBody());
            }
        } catch (Exception e) {
            log.error("Exception calling LLM service to generate summary, modelId: {}", modelId, e);
        }

        return "Failed to generate summary, please try again later";
    }

    @Override
    public String generateSummary(String conversation, String promptTemplate) {
        return generateSummary(conversation, promptTemplate, null);
    }

    @Override
    public String generateSummaryWithHistory(String conversation, String historyMemory, String promptTemplate,
            String modelId) {
        if (!isAvailable()) {
            log.warn("LLM service unavailable, cannot generate summary");
            return "LLM service unavailable, cannot generate summary";
        }

        try {
            // Get from Control PanelLLMModel config
            ModelConfigEntity llmConfig;
            if (modelId != null && !modelId.trim().isEmpty()) {
                llmConfig = modelConfigService.getModelByIdFromCache(modelId);
            } else {
                llmConfig = getDefaultLLMConfig();
            }

            if (llmConfig == null || llmConfig.getConfigJson() == null) {
                log.error("No available LLM model config found, modelId: {}", modelId);
                return "No available LLM model config found";
            }

            JSONObject configJson = llmConfig.getConfigJson();
            String baseUrl = configJson.getStr("base_url");
            String model = configJson.getStr("model_name");
            String apiKey = configJson.getStr("api_key");

            if (StringUtils.isBlank(baseUrl) || StringUtils.isBlank(apiKey)) {
                log.error("LLM config incomplete, baseUrl or apiKey empty");
                return "LLM config incomplete, cannot generate summary";
            }

            // BuildPromptWords, include historical memory
            String prompt = (promptTemplate != null ? promptTemplate : DEFAULT_SUMMARY_PROMPT)
                    .replace("{history_memory}", historyMemory != null ? historyMemory : "No memory history")
                    .replace("{conversation}", conversation);

            // Build request body
            Map<String, Object> requestBody = new HashMap<>();
            requestBody.put("model", model != null ? model : "gpt-3.5-turbo");

            Map<String, Object>[] messages = new Map[1];
            Map<String, Object> message = new HashMap<>();
            message.put("role", "user");
            message.put("content", prompt);
            messages[0] = message;

            requestBody.put("messages", messages);
            requestBody.put("temperature", 0.2);
            requestBody.put("max_tokens", 2000);

            // DisableThinking Mode
            applyThinkingDisabled(baseUrl, requestBody);

            // SendHTTPRequest
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("Authorization", "Bearer " + apiKey);

            HttpEntity<Map<String, Object>> entity = new HttpEntity<>(requestBody, headers);

            // Build completeAPI URL
            String apiUrl = baseUrl;
            if (!apiUrl.endsWith("/chat/completions")) {
                if (!apiUrl.endsWith("/")) {
                    apiUrl += "/";
                }
                apiUrl += "chat/completions";
            }

            ResponseEntity<String> response = restTemplate.exchange(
                    apiUrl, HttpMethod.POST, entity, String.class);

            if (response.getStatusCode().is2xxSuccessful()) {
                validateLlmResponse(response.getBody());
                JSONObject responseJson = JSONUtil.parseObj(response.getBody());
                JSONArray choices = responseJson.getJSONArray("choices");
                if (choices != null && choices.size() > 0) {
                    JSONObject choice = choices.getJSONObject(0);
                    JSONObject messageObj = choice.getJSONObject("message");
                    return messageObj.getStr("content");
                }
            } else {
                log.error("LLM API call failed, status code: {}, response: {}", response.getStatusCode(), response.getBody());
            }
        } catch (Exception e) {
            log.error("Exception calling LLM service to generate summary, modelId: {}", modelId, e);
        }

        return "Failed to generate summary, please try again later";
    }

    @Override
    public boolean isAvailable() {
        try {
            ModelConfigEntity defaultLLMConfig = getDefaultLLMConfig();
            if (defaultLLMConfig == null || defaultLLMConfig.getConfigJson() == null) {
                return false;
            }

            JSONObject configJson = defaultLLMConfig.getConfigJson();
            String baseUrl = configJson.getStr("base_url");
            String apiKey = configJson.getStr("api_key");

            return baseUrl != null && !baseUrl.trim().isEmpty() &&
                    apiKey != null && !apiKey.trim().isEmpty();
        } catch (Exception e) {
            log.error("Exception while checking LLM service availability:", e);
            return false;
        }
    }

    @Override
    public boolean isAvailable(String modelId) {
        try {
            if (modelId == null || modelId.trim().isEmpty()) {
                return isAvailable();
            }

            // Through SpecificModel IDGet Config
            ModelConfigEntity modelConfig = modelConfigService.getModelByIdFromCache(modelId);
            if (modelConfig == null || modelConfig.getConfigJson() == null) {
                log.warn("Specified LLM model config not found, modelId: {}", modelId);
                return false;
            }

            JSONObject configJson = modelConfig.getConfigJson();
            String baseUrl = configJson.getStr("base_url");
            String apiKey = configJson.getStr("api_key");

            return baseUrl != null && !baseUrl.trim().isEmpty() &&
                    apiKey != null && !apiKey.trim().isEmpty();
        } catch (Exception e) {
            log.error("Exception while checking LLM service availability, modelId: {}", modelId, e);
            return false;
        }
    }

    /**
     * Get default from Control PanelLLMModel config
     */
    private ModelConfigEntity getDefaultLLMConfig() {
        try {
            // Get allEnableofLLMModel config
            List<ModelConfigEntity> llmConfigs = modelConfigService.getEnabledModelsByType("LLM");
            if (llmConfigs == null || llmConfigs.isEmpty()) {
                return null;
            }

            // Prefer default config. If no default config, return firstEnableConfig of
            for (ModelConfigEntity config : llmConfigs) {
                if (config.getIsDefault() != null && config.getIsDefault() == 1) {
                    return config;
                }
            }

            return llmConfigs.get(0);
        } catch (Exception e) {
            log.error("Exception while getting LLM model config:", e);
            return null;
        }
    }

    @Override
    public String generateTitle(String conversation, String modelId) {
        if (!isAvailable()) {
            log.warn("LLM service unavailable, cannot generate title");
            return null;
        }

        try {
            ModelConfigEntity llmConfig;
            if (modelId != null && !modelId.trim().isEmpty()) {
                llmConfig = modelConfigService.getModelByIdFromCache(modelId);
            } else {
                llmConfig = getDefaultLLMConfig();
            }

            if (llmConfig == null || llmConfig.getConfigJson() == null) {
                log.error("No available LLM model config found, modelId: {}", modelId);
                return null;
            }

            JSONObject configJson = llmConfig.getConfigJson();
            String baseUrl = configJson.getStr("base_url");
            String model = configJson.getStr("model_name");
            String apiKey = configJson.getStr("api_key");

            if (StringUtils.isBlank(baseUrl) || StringUtils.isBlank(apiKey)) {
                log.error("LLM config incomplete, baseUrl or apiKey empty");
                return null;
            }

            String prompt = DEFAULT_TITLE_PROMPT.replace("{conversation}", conversation);

            Map<String, Object> requestBody = new HashMap<>();
            requestBody.put("model", model != null ? model : "gpt-3.5-turbo");

            Map<String, Object>[] messages = new Map[1];
            Map<String, Object> message = new HashMap<>();
            message.put("role", "user");
            message.put("content", prompt);
            messages[0] = message;

            requestBody.put("messages", messages);
            requestBody.put("temperature", 0.3);
            requestBody.put("max_tokens", 50);

            // DisableThinking Mode
            applyThinkingDisabled(baseUrl, requestBody);

            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);
            headers.set("Authorization", "Bearer " + apiKey);

            HttpEntity<Map<String, Object>> entity = new HttpEntity<>(requestBody, headers);

            String apiUrl = baseUrl;
            if (!apiUrl.endsWith("/chat/completions")) {
                if (!apiUrl.endsWith("/")) {
                    apiUrl += "/";
                }
                apiUrl += "chat/completions";
            }

            ResponseEntity<String> response = restTemplate.exchange(
                    apiUrl, HttpMethod.POST, entity, String.class);

            if (response.getStatusCode().is2xxSuccessful()) {
                validateLlmResponse(response.getBody());
                JSONObject responseJson = JSONUtil.parseObj(response.getBody());
                JSONArray choices = responseJson.getJSONArray("choices");
                if (choices != null && choices.size() > 0) {
                    JSONObject choice = choices.getJSONObject(0);
                    JSONObject messageObj = choice.getJSONObject("message");
                    String title = messageObj.getStr("content");
                    if (StringUtils.isNotBlank(title)) {
                        title = title.trim().replaceAll("[,.!?,:;''\"\"[]()]", "");
                        if (title.length() > 15) {
                            title = title.substring(0, 15);
                        }
                        return title;
                    }
                }
            } else {
                log.error("LLM API call failed, status code: {}, response: {}", response.getStatusCode(), response.getBody());
            }
        } catch (Exception e) {
            log.error("Exception calling LLM service to generate title, modelId: {}", modelId, e);
        }

        return null;
    }
}