package tbot.modules.agent.service;

import com.baomidou.mybatisplus.extension.service.IService;

import tbot.modules.agent.entity.AgentTemplateEntity;

/**
 * @author chenerlei
 * @description For Table [ai_agent_template(Agent config template table)Database operation for ]Service
 * @createDate 2025-03-22 11:48:18
 */
public interface AgentTemplateService extends IService<AgentTemplateEntity> {

    /**
     * Get default template
     * 
     * @return Default template entity
     */
    AgentTemplateEntity getDefaultTemplate();

    /**
     * Update model in default templateID
     * 
     * @param modelType Model Type
     * @param modelId   ModelID
     */
    void updateDefaultTemplateModelId(String modelType, String modelId);

    /**
     * Resort remaining templates after deleting template
     * 
     * @param deletedSort Sort value of deleted template
     */
    void reorderTemplatesAfterDelete(Integer deletedSort);

    /**
     * Get next available sort number (find smallest unused number)
     * 
     * @return Next available sort sequence number
     */
    Integer getNextAvailableSort();
}
