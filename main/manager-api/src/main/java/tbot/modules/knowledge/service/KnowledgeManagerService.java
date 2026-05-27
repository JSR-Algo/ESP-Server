package tbot.modules.knowledge.service;

import java.util.List;

/**
 * Knowledge base module domain orchestration service
 * Used handle cross KnowledgeBase and KnowledgeFiles complex business flow, completely solve Service Circular dependency issue between.
 */
public interface KnowledgeManagerService {

    /**
     * Cascade delete knowledge base and all subordinate documents (Include Local DB and RAGFlow Remote Data)
     * 
     * @param datasetId Knowledge base ID
     */
    void deleteDatasetWithFiles(String datasetId);

    /**
     * Batch cascade delete knowledge bases
     * 
     * @param datasetIds Knowledge base ID List
     */
    void batchDeleteDatasetsWithFiles(List<String> datasetIds);
}
