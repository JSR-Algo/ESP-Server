package tbot.modules.config.service.impl;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.stream.Collectors;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;

import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.utils.ConvertUtils;
import tbot.common.utils.JsonUtils;
import tbot.modules.agent.dao.AgentVoicePrintDao;
import tbot.modules.agent.entity.AgentContextProviderEntity;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.entity.AgentPluginMapping;
import tbot.modules.agent.entity.AgentTemplateEntity;
import tbot.modules.agent.entity.AgentVoicePrintEntity;
import tbot.modules.agent.service.AgentContextProviderService;
import tbot.modules.agent.service.AgentMcpAccessPointService;
import tbot.modules.agent.service.AgentPluginMappingService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.correctword.service.CorrectWordFileService;
import tbot.modules.agent.vo.AgentVoicePrintVO;
import tbot.modules.correctword.vo.CorrectWordSimpleVO;
import tbot.modules.config.service.ConfigService;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;
import tbot.modules.model.entity.ModelConfigEntity;
import tbot.modules.model.service.ModelConfigService;
import tbot.modules.sys.dto.SysParamsDTO;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.timbre.service.TimbreService;
import tbot.modules.timbre.vo.TimbreDetailsVO;
import tbot.modules.voiceclone.entity.VoiceCloneEntity;
import tbot.modules.voiceclone.service.VoiceCloneService;

@Service
@AllArgsConstructor
public class ConfigServiceImpl implements ConfigService {
    private final SysParamsService sysParamsService;
    private final DeviceService deviceService;
    private final ModelConfigService modelConfigService;
    private final AgentService agentService;
    private final AgentTemplateService agentTemplateService;
    private final RedisUtils redisUtils;
    private final TimbreService timbreService;
    private final AgentPluginMappingService agentPluginMappingService;
    private final AgentMcpAccessPointService agentMcpAccessPointService;
    private final AgentContextProviderService agentContextProviderService;
    private final VoiceCloneService cloneVoiceService;
    private final AgentVoicePrintDao agentVoicePrintDao;
    private final CorrectWordFileService correctWordFileService;

    @Override
    public Object getConfig(Boolean isCache) {
        if (isCache) {
            // First fromRedisGet Config
            Object cachedConfig = redisUtils.get(RedisKeys.getServerConfigKey());
            if (cachedConfig != null) {
                return cachedConfig;
            }
        }

        // Build ConfigInfo
        Map<String, Object> result = new HashMap<>();
        buildConfig(result);

        // Query default agent
        AgentTemplateEntity agent = agentTemplateService.getDefaultTemplate();
        if (agent == null) {
            throw new RenException(ErrorCode.AGENT_TEMPLATE_NOT_FOUND);
        }

        // Build module config
        buildModuleConfig(
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                agent.getVadModelId(),
                agent.getAsrModelId(),
                null,
                null,
                null,
                null,
                null,
                null,
                null,
                result,
                isCache);

        // Store config inRedis
        redisUtils.set(RedisKeys.getServerConfigKey(), result);

        return result;
    }

    @Override
    public Map<String, Object> getAgentModels(String macAddress, Map<String, String> selectedModule) {
        // Check whether admin console request
        String redisKey = RedisKeys.getTmpRegisterMacKey(macAddress);
        Object isAdminRequest = redisUtils.get(redisKey);

        if (isAdminRequest != null && "true".equals(isAdminRequest)) {
            // Management console request, returngetConfigResult of
            redisUtils.delete(redisKey); // Clean after use
            return (Map<String, Object>) getConfig(true);
        }
        // Based onMAC addressFind Device
        DeviceEntity device = deviceService.getDeviceByMacAddress(macAddress);
        if (device == null) {
            // If device, goredisCheck inside for devices needing connection
            String cachedCode = deviceService.geCodeByDeviceId(macAddress);
            if (StringUtils.isNotBlank(cachedCode)) {
                throw new RenException(ErrorCode.OTA_DEVICE_NEED_BIND, cachedCode);
            }
            throw new RenException(ErrorCode.OTA_DEVICE_NOT_FOUND);
        }

        // GetAgent info
        AgentEntity agent = agentService.getAgentById(device.getAgentId());
        if (agent == null) {
            throw new RenException(ErrorCode.AGENT_NOT_FOUND);
        }
        // GetVoice info
        String voice = null;
        String referenceAudio = null;
        String referenceText = null;
        String language = null;
        TimbreDetailsVO timbre = timbreService.get(agent.getTtsVoiceId());
        if (timbre != null) {
            voice = timbre.getTtsVoice();
            referenceAudio = timbre.getReferenceAudio();
            referenceText = timbre.getReferenceText();
            // Prefer user-selectedLanguageIf none, use first supported by voiceLanguage
            if (StringUtils.isNotBlank(agent.getTtsLanguage())) {
                language = agent.getTtsLanguage();
            } else if (StringUtils.isNotBlank(timbre.getLanguages())) {
                language = timbre.getLanguages().split(",")[0].trim();
            }
        } else {
            VoiceCloneEntity voice_print = cloneVoiceService.selectById(agent.getTtsVoiceId());
            if (voice_print != null) {
                voice = voice_print.getVoiceId();
                // Prefer user-selectedLanguage, if none, use default value
                language = StringUtils.isNotBlank(agent.getTtsLanguage()) ? agent.getTtsLanguage() : "Mandarin";
            }
        }
        // Build return data
        Map<String, Object> result = new HashMap<>();
        // Get max daily output word count per device
        String deviceMaxOutputSize = sysParamsService.getValue("device_max_output_size", true);
        result.put("device_max_output_size", deviceMaxOutputSize);

        // Get chat history config
        Integer chatHistoryConf = agent.getChatHistoryConf();
        if (agent.getMemModelId() != null && agent.getMemModelId().equals(Constant.MEMORY_NO_MEM)) {
            chatHistoryConf = Constant.ChatHistoryConfEnum.IGNORE.getCode();
        } else if (agent.getMemModelId() != null
                && !agent.getMemModelId().equals(Constant.MEMORY_NO_MEM)
                && agent.getChatHistoryConf() == null) {
            chatHistoryConf = Constant.ChatHistoryConfEnum.RECORD_TEXT_AUDIO.getCode();
        }
        result.put("chat_history_conf", chatHistoryConf);
        // If client has instantiated model, do not return
        String alreadySelectedVadModelId = selectedModule.get("VAD");
        if (alreadySelectedVadModelId != null && alreadySelectedVadModelId.equals(agent.getVadModelId())) {
            agent.setVadModelId(null);
        }
        String alreadySelectedAsrModelId = selectedModule.get("ASR");
        if (alreadySelectedAsrModelId != null && alreadySelectedAsrModelId.equals(agent.getAsrModelId())) {
            agent.setAsrModelId(null);
        }

        // Add function call parametersInfo
        if (!Objects.equals(agent.getIntentModelId(), "Intent_nointent")) {
            String agentId = agent.getId();
            List<AgentPluginMapping> pluginMappings = agentPluginMappingService.agentPluginParamsByAgentId(agentId);
            if (pluginMappings != null && !pluginMappings.isEmpty()) {
                Map<String, Object> pluginParams = new HashMap<>();
                for (AgentPluginMapping pluginMapping : pluginMappings) {
                    pluginParams.put(pluginMapping.getProviderCode(), pluginMapping.getParamInfo());
                }
                result.put("plugins", pluginParams);
            }
        }
        // GetmcpAccess point address
        String mcpEndpoint = agentMcpAccessPointService.getAgentMcpAccessAddress(agent.getId());
        if (StringUtils.isNotBlank(mcpEndpoint) && mcpEndpoint.startsWith("ws")) {
            mcpEndpoint = mcpEndpoint.replace("/mcp/", "/call/");
            result.put("mcp_endpoint", mcpEndpoint);
        }

        // GetContext source config
        AgentContextProviderEntity contextProviderEntity = agentContextProviderService.getByAgentId(agent.getId());
        if (contextProviderEntity != null && contextProviderEntity.getContextProviders() != null
                && !contextProviderEntity.getContextProviders().isEmpty()) {
            result.put("context_providers", contextProviderEntity.getContextProviders());
        }

        // Get VoiceprintInfo
        buildVoiceprintConfig(agent.getId(), result);

        // Build module config
        buildModuleConfig(
                agent.getAgentName(),
                agent.getSystemPrompt(),
                agent.getSummaryMemory(),
                voice,
                referenceAudio,
                referenceText,
                language,
                agent.getTtsVolume(),
                agent.getTtsRate(),
                agent.getTtsPitch(),
                agent.getVoiceMode(),
                agent.getGoogleLiveConfigJson(),
                agent.getVadModelId(),
                agent.getAsrModelId(),
                agent.getLlmModelId(),
                agent.getVllmModelId(),
                agent.getSlmModelId(),
                agent.getTtsModelId(),
                agent.getMemModelId(),
                agent.getIntentModelId(),
                null,
                result,
                true);

        return result;
    }

    @Override
    public List<String> getCorrectWords(String macAddress) {
        DeviceEntity device = deviceService.getDeviceByMacAddress(macAddress);
        if (device == null) {
            return Collections.emptyList();
        }
        List<CorrectWordSimpleVO> items = correctWordFileService.getAllItemsByAgentId(device.getAgentId());
        return items.stream()
                .map(item -> item.getSourceWord() + "|" + item.getTargetWord())
                .collect(Collectors.toList());
    }

    /**
     * Build ConfigInfo
     * 
     * @param config System parameter list
     * @return ConfigInfo
     */
    private Object buildConfig(Map<String, Object> config) {

        // Query all system parameters
        List<SysParamsDTO> paramsList = sysParamsService.list(new HashMap<>());

        for (SysParamsDTO param : paramsList) {
            String[] keys = param.getParamCode().split("\\.");
            Map<String, Object> current = config;

            // Traverse except last onekeyAll exceptkey
            for (int i = 0; i < keys.length - 1; i++) {
                String key = keys[i];
                if (!current.containsKey(key)) {
                    current.put(key, new HashMap<String, Object>());
                }
                current = (Map<String, Object>) current.get(key);
            }

            // Handle last onekey
            String lastKey = keys[keys.length - 1];
            String value = param.getParamValue();

            // Based onvalueTypeConverted value
            switch (param.getValueType().toLowerCase()) {
                case "number":
                    try {
                        double doubleValue = Double.parseDouble(value);
                        // If value is integer form, convert toInteger
                        if (doubleValue == (int) doubleValue) {
                            current.put(lastKey, (int) doubleValue);
                        } else {
                            current.put(lastKey, doubleValue);
                        }
                    } catch (NumberFormatException e) {
                        current.put(lastKey, value);
                    }
                    break;
                case "boolean":
                    current.put(lastKey, Boolean.parseBoolean(value));
                    break;
                case "array":
                    // Convert semicolon-separated string to number array
                    List<String> list = new ArrayList<>();
                    for (String num : value.split(";")) {
                        if (StringUtils.isNotBlank(num)) {
                            list.add(num.trim());
                        }
                    }
                    current.put(lastKey, list);
                    break;
                case "json":
                    try {
                        current.put(lastKey, JsonUtils.parseObject(value, Object.class));
                    } catch (Exception e) {
                        current.put(lastKey, value);
                    }
                    break;
                default:
                    current.put(lastKey, value);
            }
        }

        return config;
    }

    /**
     * Build voiceprint configInfo
     * 
     * @param agentId Agent ID
     * @param result  ResultMap
     */
    private void buildVoiceprintConfig(String agentId, Map<String, Object> result) {
        try {
            // Get voiceprint API address
            String voiceprintUrl = sysParamsService.getValue(Constant.SERVER_VOICE_PRINT, true);
            if (StringUtils.isBlank(voiceprintUrl) || "null".equals(voiceprintUrl)) {
                return;
            }

            // Get agent-associated voiceprintInfo(no user permission verification needed)
            List<AgentVoicePrintVO> voiceprints = getVoiceprintsByAgentId(agentId);
            if (voiceprints == null || voiceprints.isEmpty()) {
                return;
            }

            // BuildspeakersList
            List<String> speakers = new ArrayList<>();
            for (AgentVoicePrintVO voiceprint : voiceprints) {
                String speakerStr = String.format("%s,%s,%s",
                        voiceprint.getId(),
                        voiceprint.getSourceName(),
                        voiceprint.getIntroduce() != null ? voiceprint.getIntroduce() : "");
                speakers.add(speakerStr);
            }

            // Build voiceprint config
            Map<String, Object> voiceprintConfig = new HashMap<>();
            voiceprintConfig.put("url", voiceprintUrl);
            voiceprintConfig.put("speakers", speakers);

            // Get voiceprint recognition similarity threshold, default0.4
            String thresholdStr = sysParamsService.getValue("server.voiceprint_similarity_threshold", true);
            if (StringUtils.isNotBlank(thresholdStr) && !"null".equals(thresholdStr)) {
                try {
                    double threshold = Double.parseDouble(thresholdStr);
                    voiceprintConfig.put("similarity_threshold", threshold);
                } catch (NumberFormatException e) {
                    // If parsing fails, use default value0.4
                    voiceprintConfig.put("similarity_threshold", 0.4);
                }
            } else {
                voiceprintConfig.put("similarity_threshold", 0.4);
            }

            result.put("voiceprint", voiceprintConfig);
        } catch (Exception e) {
            // Voiceprint config get failure must not affect other features
            System.err.println("Failed to get voiceprint config: " + e.getMessage());
        }
    }

    /**
     * Get agent-associated voiceprintInfo
     * 
     * @param agentId Agent ID
     * @return VoiceprintInfoList
     */
    private List<AgentVoicePrintVO> getVoiceprintsByAgentId(String agentId) {
        LambdaQueryWrapper<AgentVoicePrintEntity> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(AgentVoicePrintEntity::getAgentId, agentId);
        queryWrapper.orderByAsc(AgentVoicePrintEntity::getCreateDate);
        List<AgentVoicePrintEntity> entities = agentVoicePrintDao.selectList(queryWrapper);
        return ConvertUtils.sourceToTarget(entities, AgentVoicePrintVO.class);
    }

    /**
     * Build module config
     * 
     * @param prompt         Promptword
     * @param voice          Voice
     * @param referenceAudio Reference audio path
     * @param referenceText  Reference Text
     * @param vadModelId     VADModel ID
     * @param asrModelId     ASRModel ID
     * @param llmModelId     LLMModel ID
     * @param ttsModelId     TTSModel ID
     * @param memModelId     Memory model ID
     * @param intentModelId  IntentModel ID
     * @param result         ResultMap
     */
    private void buildModuleConfig(
            String assistantName,
            String prompt,
            String summaryMemory,
            String voice,
            String referenceAudio,
            String referenceText,
            String language,
            Integer ttsVolume,
            Integer ttsRate,
            Integer ttsPitch,
            String voiceModeValue,
            String googleLiveConfigJson,
            String vadModelId,
            String asrModelId,
            String llmModelId,
            String vllmModelId,
            String slmModelId,
            String ttsModelId,
            String memModelId,
            String intentModelId,
            String ragModelId,
            Map<String, Object> result,
            boolean isCache) {
        Map<String, String> selectedModule = new HashMap<>();

        String[] modelTypes = { "VAD", "ASR", "TTS", "Memory", "Intent", "LLM", "VLLM", "SLM", "RAG" };
        String[] modelIds = { vadModelId, asrModelId, ttsModelId, memModelId, intentModelId, llmModelId, vllmModelId,
                slmModelId, ragModelId };
        String intentLLMModelId = null;
        String memLocalShortLLMModelId = null;

        for (int i = 0; i < modelIds.length; i++) {
            if (modelIds[i] == null) {
                continue;
            }
            // Key: third parameter passfalseEnsure get originalKey
            ModelConfigEntity model = modelConfigService.getModelByIdFromCache(modelIds[i]);
            if (model == null) {
                continue;
            }
            Map<String, Object> typeConfig = new HashMap<>();
            if (model.getConfigJson() != null) {
                typeConfig.put(model.getId(), model.getConfigJson());
                // If isTTSType, addprivate_voiceProperty
                if ("TTS".equals(modelTypes[i])) {
                    if (voice != null)
                        ((Map<String, Object>) model.getConfigJson()).put("private_voice", voice);
                    if (referenceAudio != null)
                        ((Map<String, Object>) model.getConfigJson()).put("ref_audio", referenceAudio);
                    if (referenceText != null)
                        ((Map<String, Object>) model.getConfigJson()).put("ref_text", referenceText);
                    if (language != null)
                        ((Map<String, Object>) model.getConfigJson()).put("language", language);
                    if (ttsVolume != null)
                        ((Map<String, Object>) model.getConfigJson()).put("ttsVolume", ttsVolume);
                    if (ttsRate != null)
                        ((Map<String, Object>) model.getConfigJson()).put("ttsRate", ttsRate);
                    if (ttsPitch != null)
                        ((Map<String, Object>) model.getConfigJson()).put("ttsPitch", ttsPitch);

                    // VolcengineVoice cloningNeed Replaceresource_id
                    Map<String, Object> map = (Map<String, Object>) model.getConfigJson();
                    if (Constant.VOICE_CLONE_HUOSHAN_DOUBLE_STREAM.equals(map.get("type"))) {
                        // Ifvoiceis”S_”Starts with, useseed-icl-1.0
                        if (voice != null && voice.startsWith("S_")) {
                            map.put("resource_id", "seed-icl-1.0");
                        }
                    }
                }
                // If isIntenttype, andtype=intent_llmthen add additional model to it
                if ("Intent".equals(modelTypes[i])) {
                    Map<String, Object> map = (Map<String, Object>) model.getConfigJson();
                    if ("intent_llm".equals(map.get("type"))) {
                        intentLLMModelId = (String) map.get("llm");
                        if (StringUtils.isNotBlank(intentLLMModelId) && intentLLMModelId.equals(llmModelId)) {
                            intentLLMModelId = null;
                        }
                    }
                    if (map.get("functions") != null) {
                        String functionStr = (String) map.get("functions");
                        if (StringUtils.isNotBlank(functionStr)) {
                            String[] functions = functionStr.split(";");
                            map.put("functions", functions);
                        }
                    }
                    System.out.println("map: " + map);
                }
                if ("Memory".equals(modelTypes[i])) {
                    Map<String, Object> map = (Map<String, Object>) model.getConfigJson();
                    if ("mem_local_short".equals(map.get("type"))) {
                        memLocalShortLLMModelId = (String) map.get("llm");
                        if (StringUtils.isNotBlank(memLocalShortLLMModelId)
                                && memLocalShortLLMModelId.equals(llmModelId)) {
                            memLocalShortLLMModelId = null;
                        }
                    }
                }
                // If isLLMtype, andintentLLMModelIdIf not empty, add extra model
                if ("LLM".equals(modelTypes[i])) {
                    if (StringUtils.isNotBlank(intentLLMModelId)) {
                        if (!typeConfig.containsKey(intentLLMModelId)) {
                            // ModifyHere: addisMaskSensitive=falseParameter
                            ModelConfigEntity intentLLM = modelConfigService.getModelByIdFromCache(intentLLMModelId);
                            typeConfig.put(intentLLM.getId(), intentLLM.getConfigJson());
                        }
                    }
                    if (StringUtils.isNotBlank(memLocalShortLLMModelId)) {
                        if (!typeConfig.containsKey(memLocalShortLLMModelId)) {
                            // ModifyHere: addisMaskSensitive=falseParameter
                            ModelConfigEntity memLocalShortLLM = modelConfigService
                                    .getModelByIdFromCache(memLocalShortLLMModelId);
                            typeConfig.put(memLocalShortLLM.getId(), memLocalShortLLM.getConfigJson());
                        }
                    }
                    // LLMalso return selectedSLMIf same nameidthen do not repeat display
                    if (StringUtils.isNotBlank(slmModelId) && !slmModelId.equals(llmModelId)) {
                        if (!typeConfig.containsKey(slmModelId)) {
                            ModelConfigEntity slmModel = modelConfigService.getModelByIdFromCache(slmModelId);
                            if (slmModel != null && slmModel.getConfigJson() != null) {
                                typeConfig.put(slmModel.getId(), slmModel.getConfigJson());
                            }
                        }
                    }
                }
            }
            result.put(modelTypes[i], typeConfig);

            selectedModule.put(modelTypes[i], model.getId());
        }

        result.put("selected_module", selectedModule);
        if (StringUtils.isNotBlank(prompt)) {
            prompt = prompt.replace("{{assistant_name}}", StringUtils.isBlank(assistantName) ? "TBOT" : assistantName);
        }
        result.put("prompt", prompt);
        result.put("summaryMemory", summaryMemory);
        appendVoiceModeConfig(voiceModeValue, googleLiveConfigJson, result);
    }

    private void appendVoiceModeConfig(
            String voiceModeValue,
            String googleLiveConfigJson,
            Map<String, Object> result) {
        String resolvedVoiceMode = resolveVoiceMode(voiceModeValue);
        Map<String, Object> googleLiveConfig = parseGoogleLiveConfig(googleLiveConfigJson);
        Map<String, Object> voiceMode = new HashMap<>();
        voiceMode.put("type", resolvedVoiceMode);
        voiceMode.put(
                "fallback_to_classic_on_error",
                resolveFallbackToClassicOnError(googleLiveConfig));
        result.put("voice_mode", voiceMode);

        if (!"google_live".equals(resolvedVoiceMode) || googleLiveConfig == null) {
            return;
        }

        result.put("google_live", googleLiveConfig);
    }

    private String resolveVoiceMode(String voiceModeValue) {
        if ("google_live".equals(voiceModeValue)) {
            return "google_live";
        }
        return "classic_pipeline";
    }

    private Map<String, Object> parseGoogleLiveConfig(String googleLiveConfigJson) {
        if (StringUtils.isBlank(googleLiveConfigJson)) {
            return null;
        }

        try {
            return JsonUtils.parseObject(googleLiveConfigJson, Map.class);
        } catch (RuntimeException ignored) {
            // Optional google_live payload must not break config reads.
            return null;
        }
    }

    private boolean resolveFallbackToClassicOnError(Map<String, Object> googleLiveConfig) {
        if (googleLiveConfig == null) {
            return true;
        }

        Object configuredValue = googleLiveConfig.get("fallback_to_classic_on_error");
        if (configuredValue instanceof Boolean) {
            return (Boolean) configuredValue;
        }
        if (configuredValue instanceof String) {
            return Boolean.parseBoolean((String) configuredValue);
        }
        return true;
    }
}
