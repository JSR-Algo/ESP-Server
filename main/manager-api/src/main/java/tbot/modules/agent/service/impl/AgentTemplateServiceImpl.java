package tbot.modules.agent.service.impl;

import org.springframework.stereotype.Service;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;

import tbot.modules.agent.dao.AgentTemplateDao;
import tbot.modules.agent.entity.AgentTemplateEntity;
import tbot.modules.agent.service.AgentTemplateService;

import java.util.List;
import java.util.Objects;
import java.util.stream.Collectors;
import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;

/**
 * @author chenerlei
 * @description For Table [ai_agent_template(Agent config template table)Database operation for ]ServiceImplement
 * @createDate 2025-03-22 11:48:18
 */
@Service
public class AgentTemplateServiceImpl extends ServiceImpl<AgentTemplateDao, AgentTemplateEntity>
        implements AgentTemplateService {

    /**
     * Get default template
     * 
     * @return Default template entity
     */
    public AgentTemplateEntity getDefaultTemplate() {
        LambdaQueryWrapper<AgentTemplateEntity> wrapper = new LambdaQueryWrapper<>();
        wrapper.orderByAsc(AgentTemplateEntity::getSort)
                .last("LIMIT 1");
        return this.getOne(wrapper);
    }

    /**
     * Update model in default templateID
     * 
     * @param modelType Model Type
     * @param modelId   ModelID
     */
    @Override
    public void updateDefaultTemplateModelId(String modelType, String modelId) {
        modelType = modelType.toUpperCase();
        // If isragModel, no need update
        if (modelType.equals("RAG")) {
            return;
        }

        UpdateWrapper<AgentTemplateEntity> wrapper = new UpdateWrapper<>();
        switch (modelType) {
            case "ASR":
                wrapper.set("asr_model_id", modelId);
                break;
            case "VAD":
                wrapper.set("vad_model_id", modelId);
                break;
            case "LLM":
                wrapper.set("llm_model_id", modelId);
                break;
            case "TTS":
                wrapper.set("tts_model_id", modelId);
                wrapper.set("tts_voice_id", null);
                break;
            case "VLLM":
                wrapper.set("vllm_model_id", modelId);
                break;
            case "MEMORY":
                wrapper.set("mem_model_id", modelId);
                break;
            case "INTENT":
                wrapper.set("intent_model_id", modelId);
                break;
        }
        wrapper.ge("sort", 0);
        update(wrapper);
    }

    @Override
    public void reorderTemplatesAfterDelete(Integer deletedSort) {
        if (deletedSort == null) {
            return;
        }
        
        // Query all records with sort value greater than deleted template
        UpdateWrapper<AgentTemplateEntity> updateWrapper = new UpdateWrapper<>();
        updateWrapper.gt("sort", deletedSort)
                    .setSql("sort = sort - 1");
        
        // Execute batch update, decrement sort values of these records1
        this.update(updateWrapper);
    }

    @Override
    public Integer getNextAvailableSort() {
        // Query all existing sort values and sort ascending
        List<Integer> sortValues = baseMapper.selectList(new QueryWrapper<AgentTemplateEntity>())
                .stream()
                .map(AgentTemplateEntity::getSort)
                .filter(Objects::nonNull)
                .sorted()
                .collect(Collectors.toList());
        
        // If no sort value, return1
        if (sortValues.isEmpty()) {
            return 1;
        }
        
        // Find smallest unused sequence number
        int expectedSort = 1;
        for (Integer sort : sortValues) {
            if (sort > expectedSort) {
                // Find missing sequence number
                return expectedSort;
            }
            expectedSort = sort + 1;
        }
        
        // If no gap, return max sequence number+1
        return expectedSort;
    }
}
