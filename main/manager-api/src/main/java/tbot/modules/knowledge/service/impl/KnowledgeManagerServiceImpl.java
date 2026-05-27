package tbot.modules.knowledge.service.impl;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tbot.modules.knowledge.service.KnowledgeBaseService;
import tbot.modules.knowledge.service.KnowledgeFilesService;
import tbot.modules.knowledge.service.KnowledgeManagerService;

import java.util.List;

@Service
@Slf4j
@RequiredArgsConstructor
public class KnowledgeManagerServiceImpl implements KnowledgeManagerService {

    private final KnowledgeBaseService knowledgeBaseService;
    private final KnowledgeFilesService knowledgeFilesService;

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void deleteDatasetWithFiles(String datasetId) {
        log.info("=== Cascade deletion started: datasetId={} ===", datasetId);

        // 1. Call file service first, clean all under this datasetDocumentRecord (include RAGFlow end)
        log.info("Step 1: Clean linked documents...");
        knowledgeFilesService.deleteDocumentsByDatasetId(datasetId);

        // 2. Then call knowledge base service, fully unregister dataset (include RAGFlow end)
        log.info("Step 2: Delete dataset body...");
        knowledgeBaseService.deleteByDatasetId(datasetId);

        log.info("=== Cascade deletion succeeded: datasetId={} ===", datasetId);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void batchDeleteDatasetsWithFiles(List<String> datasetIds) {
        if (datasetIds == null || datasetIds.isEmpty())
            return;
        log.info("=== Batch cascade delete started: count={} ===", datasetIds.size());
        for (String id : datasetIds) {
            deleteDatasetWithFiles(id);
        }
    }
}
