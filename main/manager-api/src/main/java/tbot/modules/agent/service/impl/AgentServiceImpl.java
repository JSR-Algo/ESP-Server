package tbot.modules.agent.service.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;

import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.PageData;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.service.impl.BaseServiceImpl;
import tbot.common.user.UserDetail;
import tbot.common.utils.ConvertUtils;
import tbot.common.utils.JsonUtils;
import tbot.common.utils.ToolUtil;
import tbot.modules.agent.dao.AgentDao;
import tbot.modules.agent.dao.AgentTagDao;
import tbot.modules.agent.dto.AgentCreateDTO;
import tbot.modules.agent.dto.AgentDTO;
import tbot.modules.agent.dto.AgentTagDTO;
import tbot.modules.agent.dto.AgentUpdateDTO;
import tbot.modules.agent.entity.AgentContextProviderEntity;
import tbot.modules.agent.entity.AgentEntity;
import tbot.modules.agent.entity.AgentPluginMapping;
import tbot.modules.agent.entity.AgentTagEntity;
import tbot.modules.agent.entity.AgentTemplateEntity;
import tbot.modules.agent.service.AgentChatHistoryService;
import tbot.modules.agent.service.AgentContextProviderService;
import tbot.modules.agent.service.AgentPluginMappingService;
import tbot.modules.agent.service.AgentService;
import tbot.modules.agent.service.AgentTagService;
import tbot.modules.agent.service.AgentTemplateService;
import tbot.modules.agent.vo.AgentInfoVO;
import tbot.modules.correctword.service.CorrectWordFileService;
import tbot.modules.device.entity.DeviceEntity;
import tbot.modules.device.service.DeviceService;
import tbot.modules.model.dto.ModelProviderDTO;
import tbot.modules.model.dto.VoiceDTO;
import tbot.modules.model.entity.ModelConfigEntity;
import tbot.modules.model.service.ModelConfigService;
import tbot.modules.model.service.ModelProviderService;
import tbot.modules.security.user.SecurityUser;
import tbot.modules.sys.enums.SuperAdminEnum;
import tbot.modules.timbre.service.TimbreService;

@Service
@AllArgsConstructor
public class AgentServiceImpl extends BaseServiceImpl<AgentDao, AgentEntity> implements AgentService {
    private final AgentDao agentDao;
    private final AgentTagDao agentTagDao;
    private final TimbreService timbreModelService;
    private final ModelConfigService modelConfigService;
    private final RedisUtils redisUtils;
    private final DeviceService deviceService;
    private final AgentPluginMappingService agentPluginMappingService;
    private final AgentChatHistoryService agentChatHistoryService;
    private final AgentTemplateService agentTemplateService;
    private final ModelProviderService modelProviderService;
    private final AgentContextProviderService agentContextProviderService;
    private final AgentTagService agentTagService;
    private final CorrectWordFileService correctWordFileService;

    @Override
    public PageData<AgentEntity> adminAgentList(Map<String, Object> params) {
        IPage<AgentEntity> page = agentDao.selectPage(
                getPage(params, "agent_name", true),
                new QueryWrapper<>());
        return new PageData<>(page.getRecords(), page.getTotal());
    }

    @Override
    public AgentInfoVO getAgentById(String id) {
        AgentInfoVO agent = agentDao.selectAgentInfoById(id);

        if (agent == null) {
            throw new RenException(ErrorCode.AGENT_NOT_FOUND);
        }

        if (agent.getMemModelId() != null && agent.getMemModelId().equals(Constant.MEMORY_NO_MEM)) {
            agent.setChatHistoryConf(Constant.ChatHistoryConfEnum.IGNORE.getCode());
        }
        if (agent.getChatHistoryConf() == null) {
            agent.setChatHistoryConf(Constant.ChatHistoryConfEnum.RECORD_TEXT_AUDIO.getCode());
        }

        // Query context source config
        AgentContextProviderEntity contextProviderEntity = agentContextProviderService.getByAgentId(id);
        if (contextProviderEntity != null) {
            agent.setContextProviders(contextProviderEntity.getContextProviders());
        }

        // Query replacement word fileIDList
        List<String> correctWordFileIds = correctWordFileService.getAgentCorrectWordFileIds(id);
        agent.setCorrectWordFileIds(correctWordFileIds);

        // No need extra query plugin list, already throughSQLQueried Out
        return agent;
    }

    @Override
    public boolean insert(AgentEntity entity) {
        // IfIDEmpty, auto-generate oneUUIDAsID
        if (entity.getId() == null || entity.getId().trim().isEmpty()) {
            entity.setId(UUID.randomUUID().toString().replace("-", ""));
        }

        // If agent code is empty, auto-generate code with prefix
        if (entity.getAgentCode() == null || entity.getAgentCode().trim().isEmpty()) {
            entity.setAgentCode("AGT_" + System.currentTimeMillis());
        }

        // If sort field empty, set default value0
        if (entity.getSort() == null) {
            entity.setSort(0);
        }

        return super.insert(entity);
    }

    @Override
    public void deleteAgentByUserId(Long userId) {
        UpdateWrapper<AgentEntity> wrapper = new UpdateWrapper<>();
        wrapper.eq("user_id", userId);
        baseDao.delete(wrapper);
    }

    @Override
    public List<AgentDTO> getUserAgents(Long userId, String keyword, String searchType) {
        QueryWrapper<AgentEntity> queryWrapper = new QueryWrapper<>();
        queryWrapper.eq("user_id", userId).orderByDesc("created_at");

        if (StringUtils.isNotBlank(keyword)) {
            queryWrapper.and(w -> {
                // Search by name
                w.like("agent_name", keyword);

                // byMACAddress search: check device first, then get corresponding agentID
                List<DeviceEntity> devices = Optional
                        .ofNullable(deviceService.searchDevicesByMacAddress(keyword, userId))
                        .orElseGet(ArrayList::new);
                List<String> agentIds = devices.stream()
                        .map(DeviceEntity::getAgentId)
                        .distinct()
                        .collect(Collectors.toList());
                if (ToolUtil.isNotEmpty(agentIds)) {
                    w.or().in("id", agentIds);
                }

                // Search by tag name
                List<String> tagAgentIds = agentTagService.getAgentIdsByTagName(keyword);
                if (ToolUtil.isNotEmpty(tagAgentIds)) {
                    w.or().in("id", tagAgentIds);
                }
            });
        }

        List<AgentEntity> agentEntities = baseDao.selectList(queryWrapper);
        return agentEntities.stream().map(this::buildAgentDTO).collect(Collectors.toList());
    }

    /**
     * willAgentEntityConvert toAgentDTO
     */
    private AgentDTO buildAgentDTO(AgentEntity agent) {
        AgentDTO dto = new AgentDTO();
        dto.setId(agent.getId());
        dto.setAgentName(agent.getAgentName());
        dto.setSystemPrompt(agent.getSystemPrompt());
        dto.setVoiceMode(agent.getVoiceMode());
        dto.setGoogleLiveConfigJson(agent.getGoogleLiveConfigJson());

        // Get TTS Model name
        dto.setTtsModelName(modelConfigService.getModelNameById(agent.getTtsModelId()));

        // Get LLM Model name
        dto.setLlmModelName(modelConfigService.getModelNameById(agent.getLlmModelId()));

        // Get VLLM Model name
        dto.setVllmModelName(modelConfigService.getModelNameById(agent.getVllmModelId()));

        // Get memory model name
        dto.setMemModelId(agent.getMemModelId());

        // Get TTS Voice name
        dto.setTtsVoiceName(timbreModelService.getTimbreNameById(agent.getTtsVoiceId()));

        // Get recent last connection duration of agent
        dto.setLastConnectedAt(deviceService.getLatestLastConnectionTime(agent.getId()));

        // Get device count
        dto.setDeviceCount(getDeviceCountByAgentId(agent.getId()));

        // Get tag list
        List<AgentTagEntity> tags = agentTagDao.selectByAgentId(agent.getId());
        if (ToolUtil.isNotEmpty(tags)) {
            dto.setTags(tags.stream().map(this::convertTagToDTO).collect(Collectors.toList()));
        }

        return dto;
    }

    private AgentTagDTO convertTagToDTO(AgentTagEntity entity) {
        AgentTagDTO dto = new AgentTagDTO();
        dto.setId(entity.getId());
        dto.setTagName(entity.getTagName());
        return dto;
    }

    @Override
    public Integer getDeviceCountByAgentId(String agentId) {
        if (StringUtils.isBlank(agentId)) {
            return 0;
        }

        // First fromRedisGet in
        Integer cachedCount = (Integer) redisUtils.get(RedisKeys.getAgentDeviceCountById(agentId));
        if (cachedCount != null) {
            return cachedCount;
        }

        // IfRedisIf not in, query from database
        Integer deviceCount = agentDao.getDeviceCountByAgentId(agentId);

        // Store result inRedis
        if (deviceCount != null) {
            redisUtils.set(RedisKeys.getAgentDeviceCountById(agentId), deviceCount, 60);
        }

        return deviceCount != null ? deviceCount : 0;
    }

    @Override
    public AgentEntity getDefaultAgentByMacAddress(String macAddress) {
        if (StringUtils.isEmpty(macAddress)) {
            return null;
        }
        return agentDao.getDefaultAgentByMacAddress(macAddress);
    }

    @Override
    public boolean checkAgentPermission(String agentId, Long userId) {
        if (SecurityUser.getUser() == null || SecurityUser.getUser().getId() == null) {
            return false;
        }
        // Get agent info
        AgentEntity agent = getAgentById(agentId);
        if (agent == null) {
            return false;
        }

        // If super admin, return directlytrue
        if (SecurityUser.getUser().getSuperAdmin() == SuperAdminEnum.YES.value()) {
            return true;
        }

        // Check whether agent owner
        return userId.equals(agent.getUserId());
    }

    // Based onidUpdate agent info
    @Override
    @Transactional(rollbackFor = Exception.class)
    public void updateAgentById(String agentId, AgentUpdateDTO dto) {
        // Query existing entity first
        AgentEntity existingEntity = this.getAgentById(agentId);
        if (existingEntity == null) {
            throw new RenException(ErrorCode.AGENT_NOT_FOUND);
        }

        // Only update provided non-null fields
        if (dto.getAgentName() != null) {
            existingEntity.setAgentName(dto.getAgentName());
        }
        if (dto.getAgentCode() != null) {
            existingEntity.setAgentCode(dto.getAgentCode());
        }
        if (dto.getAsrModelId() != null) {
            existingEntity.setAsrModelId(dto.getAsrModelId());
        }
        if (dto.getVadModelId() != null) {
            existingEntity.setVadModelId(dto.getVadModelId());
        }
        if (dto.getLlmModelId() != null) {
            existingEntity.setLlmModelId(dto.getLlmModelId());
        }
        if (dto.getSlmModelId() != null) {
            existingEntity.setSlmModelId(dto.getSlmModelId());
        }
        if (dto.getVllmModelId() != null) {
            existingEntity.setVllmModelId(dto.getVllmModelId());
        }
        if (dto.getTtsModelId() != null) {
            existingEntity.setTtsModelId(dto.getTtsModelId());
        }
        if (dto.getTtsVoiceId() != null) {
            existingEntity.setTtsVoiceId(dto.getTtsVoiceId());
        }
        if (dto.getTtsLanguage() != null) {
            existingEntity.setTtsLanguage(dto.getTtsLanguage());
        }
        if (dto.getVoiceMode() != null) {
            existingEntity.setVoiceMode(dto.getVoiceMode());
        }
        if (dto.getGoogleLiveConfigJson() != null) {
            existingEntity.setGoogleLiveConfigJson(dto.getGoogleLiveConfigJson());
        }
        if (dto.getTtsVolume() != null) {
            existingEntity.setTtsVolume(dto.getTtsVolume());
        }
        if (dto.getTtsRate() != null) {
            existingEntity.setTtsRate(dto.getTtsRate());
        }
        if (dto.getTtsPitch() != null) {
            existingEntity.setTtsPitch(dto.getTtsPitch());
        }
        if (dto.getMemModelId() != null) {
            existingEntity.setMemModelId(dto.getMemModelId());
        }
        if (dto.getIntentModelId() != null) {
            existingEntity.setIntentModelId(dto.getIntentModelId());
        }
        if (dto.getSystemPrompt() != null) {
            existingEntity.setSystemPrompt(dto.getSystemPrompt());
        }
        if (dto.getSummaryMemory() != null) {
            existingEntity.setSummaryMemory(dto.getSummaryMemory());
        }
        if (dto.getChatHistoryConf() != null) {
            existingEntity.setChatHistoryConf(dto.getChatHistoryConf());
        }
        if (dto.getLangCode() != null) {
            existingEntity.setLangCode(dto.getLangCode());
        }
        if (dto.getLanguage() != null) {
            existingEntity.setLanguage(dto.getLanguage());
        }
        if (dto.getSort() != null) {
            existingEntity.setSort(dto.getSort());
        }

        // Update function plugin info
        List<AgentUpdateDTO.FunctionInfo> functions = dto.getFunctions();
        if (functions != null) {
            // 1. Collect current submitted pluginId
            List<String> newPluginIds = functions.stream()
                    .map(AgentUpdateDTO.FunctionInfo::getPluginId)
                    .toList();

            // 2. Query CurrentagentAll existing mappings
            List<AgentPluginMapping> existing = agentPluginMappingService.list(
                    new QueryWrapper<AgentPluginMapping>()
                            .eq("agent_id", agentId));
            Map<String, AgentPluginMapping> existMap = existing.stream()
                    .collect(Collectors.toMap(AgentPluginMapping::getPluginId, Function.identity()));

            // 3. Construct all entities to save or update
            List<AgentPluginMapping> allToPersist = functions.stream().map(info -> {
                AgentPluginMapping m = new AgentPluginMapping();
                m.setAgentId(agentId);
                m.setPluginId(info.getPluginId());
                m.setParamInfo(JsonUtils.toJsonString(info.getParamInfo()));
                AgentPluginMapping old = existMap.get(info.getPluginId());
                if (old != null) {
                    // Already exists, setidIndicate Update
                    m.setId(old.getId());
                }
                return m;
            }).toList();

            // 4. Split: existingIDones update, noneIDgo Insert
            List<AgentPluginMapping> toUpdate = allToPersist.stream()
                    .filter(m -> m.getId() != null)
                    .toList();
            List<AgentPluginMapping> toInsert = allToPersist.stream()
                    .filter(m -> m.getId() == null)
                    .toList();

            if (!toUpdate.isEmpty()) {
                agentPluginMappingService.updateBatchById(toUpdate);
            }
            if (!toInsert.isEmpty()) {
                agentPluginMappingService.saveBatch(toInsert);
            }

            // 5. Delete plugin mappings not in this submit list
            List<Long> toDelete = existing.stream()
                    .filter(old -> !newPluginIds.contains(old.getPluginId()))
                    .map(AgentPluginMapping::getId)
                    .toList();
            if (!toDelete.isEmpty()) {
                agentPluginMappingService.removeBatchByIds(toDelete);
            }
        }

        // Set updater info
        UserDetail user = SecurityUser.getUser();
        existingEntity.setUpdater(user.getId());
        existingEntity.setUpdatedAt(new Date());

        // Update memory policy
        // Delete all records
        if (existingEntity.getMemModelId() != null && existingEntity.getMemModelId().equals(Constant.MEMORY_NO_MEM)) {
            agentChatHistoryService.deleteByAgentId(existingEntity.getId(), true, true);
            existingEntity.setSummaryMemory("");
            // Delete Memory
        } else if (existingEntity.getMemModelId() != null
                && existingEntity.getMemModelId().equals(Constant.MEMORY_MEM_REPORT_ONLY)) {
            existingEntity.setSummaryMemory("");
        }

        // Update context source config
        if (dto.getContextProviders() != null) {
            AgentContextProviderEntity contextEntity = new AgentContextProviderEntity();
            contextEntity.setAgentId(agentId);
            contextEntity.setContextProviders(dto.getContextProviders());
            agentContextProviderService.saveOrUpdateByAgentId(contextEntity);
        }

        // Update replacement word file association
        if (dto.getCorrectWordFileIds() != null) {
            correctWordFileService.saveAgentCorrectWords(agentId, dto.getCorrectWordFileIds());
        }

        boolean b = validateLLMIntentParams(dto.getLlmModelId(), dto.getIntentModelId());
        if (!b) {
            throw new RenException(ErrorCode.LLM_INTENT_PARAMS_MISMATCH);
        }
        this.updateById(existingEntity);
    }

    /**
     * Validate whether parameters for LLM and intent recognition match
     * 
     * @param llmModelId    Large language modelid
     * @param intentModelId Intent recognitionid
     * @return T Match : F Mismatch
     */
    private boolean validateLLMIntentParams(String llmModelId, String intentModelId) {
        if (StringUtils.isBlank(llmModelId)) {
            return true;
        }
        ModelConfigEntity llmModelData = modelConfigService.selectById(llmModelId);
        String type = llmModelData.getConfigJson().get("type").toString();
        // If queried large language model isopenaiOrollama, intent recognition parameter selection all OK
        if ("openai".equals(type) || "ollama".equals(type)) {
            return true;
        }
        // ExceptopenaiandollamaType, cannot selectidforIntent_function_call(function call) intent recognition
        return !"Intent_function_call".equals(intentModelId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public String createAgent(AgentCreateDTO dto) {
        // Convert to entity
        AgentEntity entity = ConvertUtils.sourceToTarget(dto, AgentEntity.class);

        // Get default template
        AgentTemplateEntity template = agentTemplateService.getDefaultTemplate();
        if (template != null) {
            // Set default value in template
            entity.setAsrModelId(template.getAsrModelId());
            entity.setVadModelId(template.getVadModelId());
            entity.setLlmModelId(template.getLlmModelId());
            entity.setVllmModelId(template.getVllmModelId());
            entity.setTtsModelId(template.getTtsModelId());

            if (template.getTtsVoiceId() == null && template.getTtsModelId() != null) {
                ModelConfigEntity ttsModel = modelConfigService.selectById(template.getTtsModelId());
                if (ttsModel != null && ttsModel.getConfigJson() != null) {
                    Map<String, Object> config = ttsModel.getConfigJson();
                    String voice = (String) config.get("voice");
                    if (StringUtils.isBlank(voice)) {
                        voice = (String) config.get("speaker");
                    }
                    VoiceDTO timbre = timbreModelService.getByVoiceCode(template.getTtsModelId(), voice);
                    if (timbre != null) {
                        template.setTtsVoiceId(timbre.getId());
                    }
                }
            }

            entity.setTtsVoiceId(template.getTtsVoiceId());
            entity.setMemModelId(template.getMemModelId());
            entity.setIntentModelId(template.getIntentModelId());
            entity.setSystemPrompt(template.getSystemPrompt());
            entity.setSummaryMemory(template.getSummaryMemory());

            // Set default based on memory model typechatHistoryConfvalue
            if (template.getMemModelId() != null) {
                if (template.getMemModelId().equals("Memory_nomem")) {
                    // Model without memory, default not record chat history
                    entity.setChatHistoryConf(0);
                } else {
                    // Model with memory, default record text and voice
                    entity.setChatHistoryConf(2);
                }
            } else {
                entity.setChatHistoryConf(template.getChatHistoryConf());
            }

            entity.setLangCode(template.getLangCode());
            entity.setLanguage(template.getLanguage());
        }

        if (entity.getSlmModelId() == null) {
            String defaultSlmModelId = getDefaultLLMModelId();
            if (defaultSlmModelId != null) {
                entity.setSlmModelId(defaultSlmModelId);
            }
        }

        // Set UserIDand creator info
        UserDetail user = SecurityUser.getUser();
        entity.setUserId(user.getId());
        entity.setCreator(user.getId());
        entity.setCreatedAt(new Date());

        // Save agent
        insert(entity);

        // Set default plugin
        List<AgentPluginMapping> toInsert = new ArrayList<>();
        // Play music, check weather, read news
        String[] pluginIds = new String[] { "SYSTEM_PLUGIN_MUSIC", "SYSTEM_PLUGIN_WEATHER",
                "SYSTEM_PLUGIN_NEWS_NEWSNOW" };
        for (String pluginId : pluginIds) {
            ModelProviderDTO provider = modelProviderService.getById(pluginId);
            if (provider == null) {
                continue;
            }
            AgentPluginMapping mapping = new AgentPluginMapping();
            mapping.setPluginId(pluginId);

            Map<String, Object> paramInfo = new HashMap<>();
            List<Map<String, Object>> fields = JsonUtils.parseObject(provider.getFields(), List.class);
            if (fields != null) {
                for (Map<String, Object> field : fields) {
                    paramInfo.put((String) field.get("key"), field.get("default"));
                }
            }
            mapping.setParamInfo(JsonUtils.toJsonString(paramInfo));
            mapping.setAgentId(entity.getId());
            toInsert.add(mapping);
        }
        // Save default plugin
        agentPluginMappingService.saveBatch(toInsert);
        return entity.getId();
    }

    private String getDefaultLLMModelId() {
        try {
            List<ModelConfigEntity> llmConfigs = modelConfigService.getEnabledModelsByType("LLM");
            if (llmConfigs == null || llmConfigs.isEmpty()) {
                return null;
            }

            for (ModelConfigEntity config : llmConfigs) {
                if (config.getIsDefault() != null && config.getIsDefault() == 1) {
                    return config.getId();
                }
            }

            return llmConfigs.get(0).getId();
        } catch (Exception e) {
            return null;
        }
    }

}
