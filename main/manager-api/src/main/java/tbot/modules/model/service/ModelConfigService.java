package tbot.modules.model.service;

import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.model.dto.LlmModelBasicInfoDTO;
import tbot.modules.model.dto.ModelBasicInfoDTO;
import tbot.modules.model.dto.ModelConfigBodyDTO;
import tbot.modules.model.dto.ModelConfigDTO;
import tbot.modules.model.entity.ModelConfigEntity;

public interface ModelConfigService extends BaseService<ModelConfigEntity> {

    List<ModelBasicInfoDTO> getModelCodeList(String modelType, String modelName);

    List<LlmModelBasicInfoDTO> getLlmModelCodeList(String modelName);

    PageData<ModelConfigDTO> getPageList(String modelType, String modelName, String page, String limit);

    ModelConfigDTO add(String modelType, String provideCode, ModelConfigBodyDTO modelConfigBodyDTO);

    ModelConfigDTO edit(String modelType, String provideCode, String id, ModelConfigBodyDTO modelConfigBodyDTO);

    void delete(String id);

    /**
     * Based onIDGet model name
     * 
     * @param id ModelID
     * @return Model name
     */
    String getModelNameById(String id);

    /**
     * Based onIDGet model config
     * 
     * @param id ModelID
     * @return Model config entity
     */
    ModelConfigEntity getModelByIdFromCache(String id);

    /**
     * Set default model
     *
     * @param modelType Model Type
     * @param isDefault Is default (1:yes,0:no)
     */
    void setDefaultModel(String modelType, int isDefault);

    /**
     * Get matchingTTSPlatform list
     *
     * @return TTSPlatform list(idandmodelName)
     */
    List<Map<String, Object>> getTtsPlatformList();

    /**
     * Get all enabled model configs by model type
     *
     * @param modelType Model type (e.g.:LLM, TTS, ASRetc.)
     * @return Enabled model config list
     */
    List<ModelConfigEntity> getEnabledModelsByType(String modelType);
}
