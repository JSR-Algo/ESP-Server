package tbot.modules.knowledge.service;

import java.util.List;
import java.util.Map;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.knowledge.dto.KnowledgeBaseDTO;
import tbot.modules.knowledge.entity.KnowledgeBaseEntity;
import tbot.modules.model.entity.ModelConfigEntity;

/**
 * Knowledge base knowledge base service interface
 */
public interface KnowledgeBaseService extends BaseService<KnowledgeBaseEntity> {

    /**
     * Paged query knowledge base list
     * 
     * @param knowledgeBaseDTO Query Conditions
     * @param page             Page number
     * @param limit            Items Per Page
     * @return Paged Data
     */
    PageData<KnowledgeBaseDTO> getPageList(KnowledgeBaseDTO knowledgeBaseDTO, Integer page, Integer limit);

    /**
     * Based onIDGet knowledge base details
     * 
     * @param id Knowledge baseID
     * @return Knowledge base details
     */
    KnowledgeBaseDTO getById(String id);

    /**
     * Add knowledge base
     * 
     * @param knowledgeBaseDTO Knowledge base info
     * @return New knowledge base
     */
    KnowledgeBaseDTO save(KnowledgeBaseDTO knowledgeBaseDTO);

    /**
     * Update knowledge base
     * 
     * @param knowledgeBaseDTO Knowledge base info
     * @return Updated knowledge base
     */
    KnowledgeBaseDTO update(KnowledgeBaseDTO knowledgeBaseDTO);

    /**
     * By knowledge baseIDQuery knowledge base
     * 
     * @param datasetId Knowledge baseID
     * @return Knowledge base details
     */
    KnowledgeBaseDTO getByDatasetId(String datasetId);

    /**
     * By knowledge baseIDCollection query knowledge base
     *
     * @param datasetIdList Knowledge baseIDCollection
     * @return Knowledge base details
     */
    List<KnowledgeBaseDTO> getByDatasetIdList(List<String> datasetIdList);

    /**
     * By knowledge baseIDDelete knowledge base
     * 
     * @param datasetId Knowledge baseID
     */
    void deleteByDatasetId(String datasetId);

    /**
     * GetRAGConfig Info
     * 
     * @param ragModelId RAGModel configID
     * @return RAGConfig Info
     */
    Map<String, Object> getRAGConfig(String ragModelId);

    /**
     * By knowledge baseIDGet correspondingRAGConfig
     * 
     * @param datasetId Knowledge baseID
     * @return RAGConfig
     */
    Map<String, Object> getRAGConfigByDatasetId(String datasetId);

    /**
     * GetRAGModel list
     * 
     * @return RAGModel list
     */
    List<ModelConfigEntity> getRAGModels();

    /**
     * Update knowledge base statistics (Used for file service callback)
     * 
     * @param datasetId  Knowledge baseID
     * @param docDelta   Document count increment
     * @param chunkDelta Chunk count increment
     * @param tokenDelta TokenCount increment
     */
    void updateStatistics(String datasetId, Integer docDelta, Long chunkDelta, Long tokenDelta);
}