package tbot.modules.knowledge.dao;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import tbot.common.dao.BaseDao;
import tbot.modules.knowledge.entity.KnowledgeBaseEntity;

/**
 * Knowledge base knowledge base
 */
@Mapper
public interface KnowledgeBaseDao extends BaseDao<KnowledgeBaseEntity> {

    /**
     * By knowledge baseIDDelete related plugin mapping records
     * 
     * @param knowledgeBaseId Knowledge baseID
     */
    void deletePluginMappingByKnowledgeBaseId(@Param("knowledgeBaseId") String knowledgeBaseId);

    /**
     * Generic dimension atomic update knowledge base statistics
     * 
     * @param datasetId  DatasetID
     * @param docDelta   Document count increment
     * @param chunkDelta Chunk count increment
     * @param tokenDelta TokenCount increment
     */
    void updateStatsAfterChange(@Param("datasetId") String datasetId,
            @Param("docDelta") Integer docDelta,
            @Param("chunkDelta") Long chunkDelta,
            @Param("tokenDelta") Long tokenDelta);

}