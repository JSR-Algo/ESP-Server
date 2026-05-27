package tbot.modules.knowledge.service.impl;

import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.BeanUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;

import lombok.extern.slf4j.Slf4j;
import tbot.common.exception.ErrorCode;
import org.springframework.util.CollectionUtils;
import tbot.common.exception.RenException;
import tbot.modules.knowledge.dto.KnowledgeFilesDTO;
import tbot.modules.knowledge.dto.document.ChunkDTO;
import tbot.modules.knowledge.dto.document.RetrievalDTO;
import tbot.modules.knowledge.dto.document.DocumentDTO;
import tbot.common.page.PageData;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.service.impl.BaseServiceImpl;
import tbot.modules.knowledge.dao.DocumentDao;
import tbot.modules.knowledge.entity.DocumentEntity;
import tbot.modules.knowledge.rag.KnowledgeBaseAdapter;
import tbot.modules.knowledge.rag.KnowledgeBaseAdapterFactory;
import tbot.modules.knowledge.service.KnowledgeBaseService;
import tbot.modules.knowledge.service.KnowledgeFilesService;

@Service
@Slf4j
public class KnowledgeFilesServiceImpl extends BaseServiceImpl<DocumentDao, DocumentEntity>
        implements KnowledgeFilesService {

    private final KnowledgeBaseService knowledgeBaseService;
    private final DocumentDao documentDao;
    private final ObjectMapper objectMapper;
    private final RedisUtils redisUtils;

    public KnowledgeFilesServiceImpl(KnowledgeBaseService knowledgeBaseService,
            DocumentDao documentDao,
            ObjectMapper objectMapper,
            RedisUtils redisUtils) {
        this.knowledgeBaseService = knowledgeBaseService;
        this.documentDao = documentDao;
        this.objectMapper = objectMapper;
        this.redisUtils = redisUtils;
    }

    @Lazy
    @Autowired
    private KnowledgeFilesService self;

    @Override
    public Map<String, Object> getRAGConfig(String ragModelId) {
        return knowledgeBaseService.getRAGConfig(ragModelId);
    }

    @Override
    public PageData<KnowledgeFilesDTO> getPageList(KnowledgeFilesDTO knowledgeFilesDTO, Integer page, Integer limit) {
        log.info("=== Start fetching knowledge base document list (Local-First optimized version) ===");
        String datasetId = knowledgeFilesDTO.getDatasetId();
        if (StringUtils.isBlank(datasetId)) {
            throw new RenException(ErrorCode.RAG_DATASET_ID_AND_MODEL_ID_NOT_NULL);
        }

        // Full reconciliation sync: fromRAGFlowPull RemoteDocumentReal-time sync ensures immediate remote change awareness
        try {
            self.syncDocumentsFromRAG(datasetId);
        } catch (Exception e) {
            log.warn("Failed to fully sync documents from RAGFlow (does not affect local queries): datasetId={}, error={}", datasetId, e.getMessage());
        }

        // 1. Get local shadow table data (MyBatis-Plus Pagination)
        Page<DocumentEntity> pageParams = new Page<>(page, limit);
        QueryWrapper<DocumentEntity> queryWrapper = new QueryWrapper<>();
        queryWrapper.eq("dataset_id", datasetId);
        if (StringUtils.isNotBlank(knowledgeFilesDTO.getName())) {
            queryWrapper.like("name", knowledgeFilesDTO.getName());
        }
        if (StringUtils.isNotBlank(knowledgeFilesDTO.getRun())) {
            queryWrapper.eq("run", knowledgeFilesDTO.getRun());
        }
        if (StringUtils.isNotBlank(knowledgeFilesDTO.getStatus())) {
            queryWrapper.eq("status", knowledgeFilesDTO.getStatus());
        }
        queryWrapper.orderByDesc("created_at");

        // 2. Execute local query
        Page<DocumentEntity> iPage = documentDao.selectPage(pageParams, queryWrapper);

        // 3. Manual Convert DTO
        List<KnowledgeFilesDTO> dtoList = new ArrayList<>();
        for (DocumentEntity entity : iPage.getRecords()) {
            dtoList.add(convertEntityToDTO(entity));
        }
        PageData<KnowledgeFilesDTO> pageData = new PageData<>(dtoList, iPage.getTotal());

        // 4. DynamicStatusSync (With rate limit and protection)
        // [Bug Fix] P1: Expand sync whitelist,CANCEL/FAIL also allow low-frequency sync for self-healing
        if (pageData.getList() != null && !pageData.getList().isEmpty()) {
            KnowledgeBaseAdapter adapter = null;
            for (KnowledgeFilesDTO dto : pageData.getList()) {
                String runStatus = dto.getRun();
                // High-priority sync: RUNNING/UNSTART (5secondsColdbut)
                boolean isActiveSync = "RUNNING".equals(runStatus) || "UNSTART".equals(runStatus);
                // Low-frequency self-heal sync: CANCEL/FAIL (60secondsColdbut), PreventErrorStatusPermanently Locked
                boolean isRecoverySync = "CANCEL".equals(runStatus) || "FAIL".equals(runStatus);
                boolean needSync = isActiveSync || isRecoverySync;

                if (needSync) {
                    // Rate limit protection: activeStatus 5 secondsColdbut self-healStatus 60 secondsColdbut
                    long cooldownMs = isActiveSync ? 5000 : 60000;
                    DocumentEntity localEntity = documentDao.selectOne(new QueryWrapper<DocumentEntity>()
                            .eq("document_id", dto.getDocumentId()));
                    if (localEntity != null && localEntity.getLastSyncAt() != null) {
                        long diff = System.currentTimeMillis() - localEntity.getLastSyncAt().getTime();
                        if (diff < cooldownMs) {
                            continue;
                        }
                    }

                    // Lazy init adapter, create only when sync needed
                    if (adapter == null) {
                        try {
                            Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);
                            adapter = KnowledgeBaseAdapterFactory.getAdapter(extractAdapterType(ragConfig), ragConfig);
                        } catch (Exception e) {
                            log.warn("Sync interrupted: cannot initialize adapter, {}", e.getMessage());
                            break;
                        }
                    }
                    // [Key Fix] Record before sync Token Count, for calculating increment
                    Long oldTokenCount = dto.getTokenCount() != null ? dto.getTokenCount() : 0L;

                    syncDocumentStatusWithRAG(dto, adapter);

                    // Calculate increment andUpdate knowledge baseStats (Keep consistent with scheduled task)
                    Long newTokenCount = dto.getTokenCount() != null ? dto.getTokenCount() : 0L;
                    Long tokenDelta = newTokenCount - oldTokenCount;
                    if (tokenDelta != 0) {
                        knowledgeBaseService.updateStatistics(datasetId, 0, 0L, tokenDelta);
                        log.info("Lazy-load sync: fix knowledge base stats, docId={}, tokenDelta={}", dto.getDocumentId(), tokenDelta);
                    }
                }
            }
        }

        log.info("Document list fetched successfully, total: {}", pageData.getTotal());
        return pageData;
    }

    /**
     * Convert local record entity toDTOmanually align inconsistent fields (size -> fileSize, type -> fileType)
     */
    private KnowledgeFilesDTO convertEntityToDTO(DocumentEntity entity) {
        if (entity == null) {
            return null;
        }
        KnowledgeFilesDTO dto = new KnowledgeFilesDTO();
        // 1. Base field copy
        BeanUtils.copyProperties(entity, dto);

        // Issue 2: Fix ID Semantics. Frontend usually uses id As OperationPrimary key.
        // In this module, always treat remote documentId Map to DTO of id, ensure frontend in details/Deletewhen operations ID Consistent.
        dto.setId(entity.getDocumentId());

        // 2. Convert local record entity toDTOmanually align inconsistent fields (size -> fileSize, type -> fileType)
        dto.setFileSize(entity.getSize());
        dto.setFileType(entity.getType());
        dto.setRun(entity.getRun());
        dto.setChunkCount(entity.getChunkCount());
        dto.setTokenCount(entity.getTokenCount());
        dto.setError(entity.getError());

        // 3. CustomMetadata JSON Deserialize (Issue 3)
        if (StringUtils.isNotBlank(entity.getMetaFields())) {
            try {
                dto.setMetaFields(objectMapper.readValue(entity.getMetaFields(),
                        new TypeReference<Map<String, Object>>() {
                        }));
            } catch (Exception e) {
                log.warn("Failed to deserialize MetaFields, entityId: {}, error: {}", entity.getId(), e.getMessage());
            }
        }

        // 4. Parse config JSON Deserialize
        if (StringUtils.isNotBlank(entity.getParserConfig())) {
            try {
                dto.setParserConfig(objectMapper.readValue(entity.getParserConfig(),
                        new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {
                        }));
            } catch (Exception e) {
                log.warn("Failed to deserialize ParserConfig, entityId: {}, error: {}", entity.getId(), e.getMessage());
            }
        }
        return dto;

    }

    /**
     * SyncDocumentStatusandRAGActualStatus
     * OptimizeStatusSync logic, ensureParsingStatusCan display normally
     * Only whenDocumentHas slices and parse time exceeds30Update to complete only after secondsStatus
     */
    /**
     * SyncDocumentStatusandRAGActualStatus (Enhanced: support external adapter input)
     */
    private void syncDocumentStatusWithRAG(KnowledgeFilesDTO dto, KnowledgeBaseAdapter adapter) {
        if (dto == null || StringUtils.isBlank(dto.getDocumentId()) || adapter == null) {
            return;
        }

        String documentId = dto.getDocumentId();
        String datasetId = dto.getDatasetId();

        try {
            // Use strong type ListReq Coordinate ID Get by filterStatus
            DocumentDTO.ListReq listReq = DocumentDTO.ListReq.builder()
                    .id(documentId)
                    .page(1)
                    .pageSize(1)
                    .build();

            PageData<KnowledgeFilesDTO> remoteList = adapter.getDocumentList(datasetId, listReq);

            if (remoteList != null && remoteList.getList() != null && !remoteList.getList().isEmpty()) {
                KnowledgeFilesDTO remoteDto = remoteList.getList().get(0);
                String remoteStatus = remoteDto.getStatus();

                // CoreStatusAlign judgment logic
                boolean statusChanged = remoteStatus != null && !remoteStatus.equals(dto.getStatus());
                boolean runChanged = remoteDto.getRun() != null && !remoteDto.getRun().equals(dto.getRun());
                boolean isProcessing = "RUNNING".equals(remoteDto.getRun()) || "UNSTART".equals(remoteDto.getRun());

                // As long asStatusChanged, or runningStatusChanged, or file still inParsing(real-time progress refresh), execute sync
                if (statusChanged || runChanged || isProcessing) {
                    log.info("Shadow sync: status change={}, parsing={}, document={}, latest status={}, progress={}",
                            statusChanged, isProcessing, documentId, remoteStatus, remoteDto.getProgress());

                    // 1. Sync Memory DTO
                    dto.setStatus(remoteStatus);
                    dto.setRun(remoteDto.getRun());
                    dto.setProgress(remoteDto.getProgress());
                    dto.setChunkCount(remoteDto.getChunkCount());
                    dto.setTokenCount(remoteDto.getTokenCount());
                    dto.setError(remoteDto.getError());
                    dto.setProcessDuration(remoteDto.getProcessDuration());
                    dto.setThumbnail(remoteDto.getThumbnail());

                    // 2. Sync local shadow table
                    UpdateWrapper<DocumentEntity> updateWrapper = new UpdateWrapper<DocumentEntity>()
                            .set("status", remoteStatus)
                            .set("run", remoteDto.getRun())
                            .set("progress", remoteDto.getProgress())
                            .set("chunk_count", remoteDto.getChunkCount())
                            .set("token_count", remoteDto.getTokenCount())
                            .set("error", remoteDto.getError())
                            .set("process_duration", remoteDto.getProcessDuration())
                            .set("thumbnail", remoteDto.getThumbnail())
                            .eq("document_id", documentId)
                            .eq("dataset_id", datasetId);

                    // SerializeMetadataSync
                    if (remoteDto.getMetaFields() != null) {
                        try {
                            updateWrapper.set("meta_fields",
                                    objectMapper.writeValueAsString(remoteDto.getMetaFields()));
                        } catch (Exception e) {
                            log.warn("Sync metadata serialization failed: {}", e.getMessage());
                        }
                    }

                    // Sync First RAG Side'sUpdate time , avoid local sync behavior overwriting businessModifyTime
                    Date lastUpdate = remoteDto.getUpdatedAt() != null ? remoteDto.getUpdatedAt() : new Date();
                    updateWrapper.set("updated_at", lastUpdate);
                    updateWrapper.set("last_sync_at", new Date()); // Record shadow DB sync time

                    documentDao.update(null, updateWrapper);
                }
            } else {
                // Issue 6: Remote list empty, may beDocument deletedMay also be adapter call issue
                // [Bug Fix] P2: Mark only when remote truly returns legal empty list CANCEL
                // Update Both last_sync_at, with P1 ColdCooldown mechanism prevents frequent false positives
                log.warn("Remote sync perception: RAGFlow returned empty document list, docId={}, current local status={}",
                        documentId, dto.getRun());
                dto.setRun("CANCEL");
                dto.setError("Document already deleted in remote service");

                documentDao.update(null, new UpdateWrapper<DocumentEntity>()
                        .set("run", "CANCEL")
                        .set("error", "Document already deleted in remote service")
                        .set("updated_at", new Date())
                        .set("last_sync_at", new Date())
                        .eq("document_id", documentId));
            }
        } catch (Exception e) {
            // [Bug Fix] P2: Adapter callExceptiondo not mark when CANCEL, avoid network/Deserialization issue caused misjudgment
            // Only log, retry next sync cycle
            log.warn("Adapter call failed when syncing document status (not marking CANCEL), documentId: {}, error: {}",
                    documentId, e.getMessage());
        }
    }

    @Override
    public DocumentDTO.InfoVO getByDocumentId(String documentId, String datasetId) {
        if (StringUtils.isBlank(documentId) || StringUtils.isBlank(datasetId)) {
            throw new RenException(ErrorCode.RAG_DATASET_ID_AND_MODEL_ID_NOT_NULL);
        }

        log.info("=== Start getting document by documentId ===");
        log.info("documentId: {}, datasetId: {}", documentId, datasetId);

        try {
            // GetRAGConfig
            Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);

            // Extract adapter type
            String adapterType = extractAdapterType(ragConfig);

            // Use adapter factory to get adapter instance
            KnowledgeBaseAdapter adapter = KnowledgeBaseAdapterFactory.getAdapter(adapterType, ragConfig);

            // Use adapter getDocumentDetails
            DocumentDTO.InfoVO info = adapter.getDocumentById(datasetId, documentId);

            if (info != null) {
                log.info("Got document details successfully, documentId: {}", documentId);
                return info;
            } else {
                throw new RenException(ErrorCode.Knowledge_Base_RECORD_NOT_EXISTS);
            }

        } catch (Exception e) {
            log.error("Failed to get document by documentId: {}", e.getMessage(), e);
            String errorMessage = e.getMessage() != null ? e.getMessage() : "null";
            if (e instanceof RenException) {
                throw (RenException) e;
            }
            throw new RenException(ErrorCode.RAG_API_ERROR, errorMessage);
        } finally {
            log.info("=== Get document operation by documentId ended ===");
        }
    }

    @Override
    public KnowledgeFilesDTO uploadDocument(String datasetId, MultipartFile file, String name,
            Map<String, Object> metaFields, String chunkMethod,
            Map<String, Object> parserConfig) {
        if (StringUtils.isBlank(datasetId) || file == null || file.isEmpty()) {
            throw new RenException(ErrorCode.PARAMS_GET_ERROR);
        }

        log.info("=== Start document upload operation (strong consistency optimization) ===");

        // 1. Preparation (Non-transactional)
        String fileName = StringUtils.isNotBlank(name) ? name : file.getOriginalFilename();
        if (StringUtils.isBlank(fileName)) {
            throw new RenException(ErrorCode.RAG_FILE_NAME_NOT_NULL);
        }

        log.info("1. Start remote upload: datasetId={}, fileName={}", datasetId, fileName);

        // Get adapter (Non-transactional)
        Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);
        KnowledgeBaseAdapter adapter = KnowledgeBaseAdapterFactory.getAdapter(extractAdapterType(ragConfig), ragConfig);

        // Build strong-typed request DTO
        DocumentDTO.UploadReq uploadReq = DocumentDTO.UploadReq.builder()
                .datasetId(datasetId)
                .file(file)
                .name(fileName)
                .metaFields(metaFields)
                .build();

        // ConvertChunking method (String -> Enum)
        if (StringUtils.isNotBlank(chunkMethod)) {
            try {
                uploadReq.setChunkMethod(DocumentDTO.InfoVO.ChunkMethod.valueOf(chunkMethod.toUpperCase()));
            } catch (Exception e) {
                log.warn("Invalid chunking method: {}, will use backend default config", chunkMethod);
            }
        }

        // ConvertParse config (Map -> DTO)
        if (parserConfig != null && !parserConfig.isEmpty()) {
            uploadReq.setParserConfig(objectMapper.convertValue(parserConfig, DocumentDTO.InfoVO.ParserConfig.class));
        }

        // Execute remote upload (Time cost IO, outside transaction)
        KnowledgeFilesDTO result = adapter.uploadDocument(uploadReq);

        if (result == null || StringUtils.isBlank(result.getDocumentId())) {
            throw new RenException(ErrorCode.RAG_API_ERROR, "Remote upload succeeded but no valid DocumentID returned");
        }

        // 2. Local persistence (Pass self Call to activate @Transactional Proxy)
        log.info("2. Sync and save local shadow record: documentId={}", result.getDocumentId());
        self.saveDocumentShadow(datasetId, result, fileName, chunkMethod, parserConfig);

        log.info("=== Document upload and shadow record saved successfully ===");
        return result;
    }

    /**
     * AtomicSaveShadow record (Upsert Semantics)
     * if document_id Update if exists, insert if not, avoid UNIQUE Constraint Conflict
     *
     * @return true=New insert, false=Update existing record
     */
    @Transactional(rollbackFor = Exception.class)
    public boolean saveDocumentShadow(String datasetId, KnowledgeFilesDTO result, String originalName, String chunkMethod,
            Map<String, Object> parserConfig) {
        DocumentEntity entity = new DocumentEntity();
        entity.setDatasetId(datasetId);
        entity.setDocumentId(result.getDocumentId());
        entity.setName(StringUtils.isNotBlank(result.getName()) ? result.getName() : originalName);
        entity.setSize(result.getFileSize());
        entity.setType(getFileType(entity.getName()));
        entity.setChunkMethod(chunkMethod);

        if (parserConfig != null) {
            try {
                entity.setParserConfig(objectMapper.writeValueAsString(parserConfig));
            } catch (Exception e) {
                log.warn("Failed to deserialize config: {}", e.getMessage());
            }
        }

        entity.setStatus(result.getStatus() != null ? result.getStatus() : "1");
        entity.setRun(result.getRun());
        entity.setProgress(result.getProgress());
        entity.setThumbnail(result.getThumbnail());
        entity.setProcessDuration(result.getProcessDuration());
        entity.setSourceType(result.getSourceType());
        entity.setError(result.getError());
        entity.setChunkCount(result.getChunkCount());
        entity.setTokenCount(result.getTokenCount());
        entity.setEnabled(1);

        // PersistMetadata
        if (result.getMetaFields() != null) {
            try {
                entity.setMetaFields(objectMapper.writeValueAsString(result.getMetaFields()));
            } catch (Exception e) {
                log.warn("Persist shadow metadata failed: {}", e.getMessage());
            }
        }

        // Sync First RAG Side'sTimestampif none, use local time
        entity.setCreatedAt(result.getCreatedAt() != null ? result.getCreatedAt() : new Date());
        entity.setUpdatedAt(result.getUpdatedAt() != null ? result.getUpdatedAt() : new Date());

        // Upsert: Check document_id Whether exists; update if exists, insert if not
        DocumentEntity existing = documentDao.selectOne(
                new QueryWrapper<DocumentEntity>().eq("document_id", entity.getDocumentId()));

        if (existing != null) {
            entity.setId(existing.getId());
            entity.setCreatedAt(existing.getCreatedAt()); // Keep OriginalCreation time
            documentDao.updateById(entity);
            log.info("Shadow record updated: documentId={}", entity.getDocumentId());
            return false;
        } else {
            documentDao.insert(entity);
            // Increment dataset when adding recordTotal documentsStats
            knowledgeBaseService.updateStatistics(datasetId, 1, 0L, 0L);
            log.info("Shadow record inserted: documentId={}, datasetId={}", entity.getDocumentId(), datasetId);
            return true;
        }
    }

    @Override
    @Transactional(propagation = Propagation.NOT_SUPPORTED)
    public void deleteDocuments(String datasetId, DocumentDTO.BatchIdReq req) {
        if (StringUtils.isBlank(datasetId) || req == null || req.getIds() == null || req.getIds().isEmpty()) {
            throw new RenException(ErrorCode.RAG_DATASET_ID_AND_MODEL_ID_NOT_NULL);
        }

        List<String> documentIds = req.getIds();
        log.info("=== Start batch delete documents: datasetId={}, count={} ===", datasetId, documentIds.size());

        // 1. Batch permission andStatusPre-review
        List<DocumentEntity> entities = documentDao.selectList(
                new QueryWrapper<DocumentEntity>()
                        .eq("dataset_id", datasetId)
                        .in("document_id", documentIds));

        if (entities.size() != documentIds.size()) {
            log.warn("Some documents do not exist or ownership is abnormal: expected={}, actual={}", documentIds.size(), entities.size());
            throw new RenException(ErrorCode.NO_PERMISSION);
        }

        long totalChunkDelta = 0;
        long totalTokenDelta = 0;

        for (DocumentEntity entity : entities) {
            // Intercept OngoingParsingofDocumentofDeleteRequest
            // [Bug Fix] DetermineParsingShould use run Field(RUNNING), Rather than status Field
            // status="1" is"Enabled/normal"Meaning of, Not"Parsing"
            if ("RUNNING".equals(entity.getRun())) {
                log.warn("Intercept delete request for parsing file: docId={}", entity.getDocumentId());
                throw new RenException(ErrorCode.RAG_DOCUMENT_PARSING_DELETE_ERROR);
            }
            totalChunkDelta += entity.getChunkCount() != null ? entity.getChunkCount() : 0L;
            totalTokenDelta += entity.getTokenCount() != null ? entity.getTokenCount() : 0L;
        }

        // 2. Get adapter (Non-transactional)
        Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);
        KnowledgeBaseAdapter adapter = KnowledgeBaseAdapterFactory.getAdapter(extractAdapterType(ragConfig), ragConfig);

        // 3. Execute RemoteDelete
        try {
            adapter.deleteDocument(datasetId, req);
            log.info("Remote batch delete request successful");
        } catch (Exception e) {
            log.warn("Remote delete request partially or fully failed: {}", e.getMessage());
        }

        // 4. Atomically clean local shadow records and sync stats
        self.deleteDocumentShadows(documentIds, datasetId, totalChunkDelta, totalTokenDelta);

        // 5. Clear Cache
        try {
            String cacheKey = RedisKeys.getKnowledgeBaseCacheKey(datasetId);
            redisUtils.delete(cacheKey);
            log.info("Evicted dataset cache: {}", cacheKey);
        } catch (Exception e) {
            log.warn("Failed to evict Redis cache: {}", e.getMessage());
        }

        log.info("=== Batch document cleanup complete ===");
    }

    /**
     * Batch atomicDeleteshadow record and sync parent table statistics
     */
    @Transactional(rollbackFor = Exception.class)
    public void deleteDocumentShadows(List<String> documentIds, String datasetId, Long chunkDelta, Long tokenDelta) {
        // 1. PhysicalDeleteRecord
        int deleted = documentDao.delete(
                new QueryWrapper<DocumentEntity>()
                        .eq("dataset_id", datasetId)
                        .in("document_id", documentIds));

        if (deleted > 0) {
            // 2. Sync update dataset statsInfo
            knowledgeBaseService.updateStatistics(datasetId, -documentIds.size(), -chunkDelta, -tokenDelta);
            log.info("Synced dataset stat deduction: datasetId={}, chunks={}, tokens={}", datasetId, chunkDelta, tokenDelta);
        }
    }

    /**
     * GetFile type - SupportRAGFour typesDocumentFormat Type
     */
    private String getFileType(String fileName) {
        if (StringUtils.isBlank(fileName)) {
            log.warn("Filename empty, returning unknown type");
            return "unknown";
        }

        try {
            int lastDotIndex = fileName.lastIndexOf('.');
            if (lastDotIndex > 0 && lastDotIndex < fileName.length() - 1) {
                String extension = fileName.substring(lastDotIndex + 1).toLowerCase();

                // DocumentFormat Type
                String[] documentTypes = { "pdf", "doc", "docx", "txt", "md", "mdx" };
                String[] spreadsheetTypes = { "csv", "xls", "xlsx" };
                String[] presentationTypes = { "ppt", "pptx" };

                // CheckDocument type
                for (String type : documentTypes) {
                    if (type.equals(extension)) {
                        return "document";
                    }
                }

                // Check table type
                for (String type : spreadsheetTypes) {
                    if (type.equals(extension)) {
                        return "spreadsheet";
                    }
                }
                // Check slide type
                for (String type : presentationTypes) {
                    if (type.equals(extension)) {
                        return "presentation";
                    }
                }
                // Return original extension asFile type
                return extension;
            }
            return "unknown";
        } catch (Exception e) {
            log.error("Failed to get file type: ", e);
            return "unknown";
        }
    }

    /**
     * fromRAGExtract adapter type from config
     */
    private String extractAdapterType(Map<String, Object> config) {
        if (config == null) {
            throw new RenException(ErrorCode.RAG_CONFIG_NOT_FOUND);
        }

        // Extract from configtypeField
        String adapterType = (String) config.get("type");
        if (StringUtils.isBlank(adapterType)) {
            throw new RenException(ErrorCode.RAG_ADAPTER_TYPE_NOT_FOUND);
        }

        // Verify adapter type alreadyRegister
        if (!KnowledgeBaseAdapterFactory.isAdapterTypeRegistered(adapterType)) {
            throw new RenException(ErrorCode.RAG_ADAPTER_TYPE_NOT_SUPPORTED, "Adapter type not registered: " + adapterType);
        }

        return adapterType;
    }

    @Override
    public boolean parseDocuments(String datasetId, List<String> documentIds) {
        if (StringUtils.isBlank(datasetId) || documentIds == null || documentIds.isEmpty()) {
            throw new RenException(ErrorCode.RAG_DATASET_ID_AND_MODEL_ID_NOT_NULL);
        }

        log.info("=== Start parsing document (chunking) ===");
        log.info("datasetId: {}, documentIds: {}", datasetId, documentIds);

        try {
            // GetRAGConfig
            Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);

            // Extract adapter type
            String adapterType = extractAdapterType(ragConfig);

            // Get knowledge base adapter
            KnowledgeBaseAdapter adapter = KnowledgeBaseAdapterFactory.getAdapter(adapterType, ragConfig);

            log.debug("Parse document parameters: documentIds: {}", documentIds);

            // Call adapter parseDocument
            boolean result = adapter.parseDocuments(datasetId, documentIds);

            if (result) {
                log.info("Document parsing command sent successfully, preparing to sync local shadow library status, datasetId: {}, documentIds: {}", datasetId, documentIds);
                // Update local shadow immediately after command succeedsStatusfor RUNNING and Parsing(1), ensure Local-First List can feedback immediately
                documentDao.update(null, new UpdateWrapper<DocumentEntity>()
                        .set("run", "RUNNING")
                        .set("status", "1")
                        .set("updated_at", new Date())
                        .eq("dataset_id", datasetId)
                        .in("document_id", documentIds));

                log.info("Document local status updated to RUNNING");
            } else {
                log.error("Document parsing failed, datasetId: {}, documentIds: {}", datasetId, documentIds);
                throw new RenException(ErrorCode.RAG_API_ERROR, "Document parsing failed");
            }

            return result;

        } catch (Exception e) {
            log.error("Failed to parse document: {}", e.getMessage(), e);
            String errorMessage = e.getMessage() != null ? e.getMessage() : "null";
            if (e instanceof RenException) {
                throw (RenException) e;
            }
            throw new RenException(ErrorCode.RAG_API_ERROR, errorMessage);
        } finally {
            log.info("=== Parse document operation ended ===");
        }
    }

    @Override
    public ChunkDTO.ListVO listChunks(String datasetId, String documentId, ChunkDTO.ListReq req) {
        if (StringUtils.isBlank(datasetId) || StringUtils.isBlank(documentId)) {
            throw new RenException(ErrorCode.RAG_DATASET_ID_AND_MODEL_ID_NOT_NULL);
        }

        log.info("=== Start listing slices: datasetId={}, documentId={}, req={} ===", datasetId, documentId, req);

        try {
            Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);
            KnowledgeBaseAdapter adapter = KnowledgeBaseAdapterFactory.getAdapter(extractAdapterType(ragConfig),
                    ragConfig);

            ChunkDTO.ListVO result = adapter.listChunks(datasetId, documentId, req);
            log.info("Chunk list fetched successfully: datasetId={}, total={}", datasetId, result.getTotal());
            return result;
        } catch (Exception e) {
            log.error("Failed to list chunks: {}", e.getMessage(), e);
            String errorMessage = e.getMessage() != null ? e.getMessage() : "null";
            if (e instanceof RenException) {
                throw (RenException) e;
            }
            throw new RenException(ErrorCode.RAG_API_ERROR, errorMessage);
        } finally {
            log.info("=== List chunks operation ended ===");
        }
    }

    @Override
    public RetrievalDTO.ResultVO retrievalTest(RetrievalDTO.TestReq req) {
        if (CollectionUtils.isEmpty(req.getDatasetIds())) {
            throw new RenException("No knowledge base specified for recall test");
        }

        log.info("=== Start recall test: req={} ===", req);

        try {
            Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(req.getDatasetIds().get(0));
            KnowledgeBaseAdapter adapter = KnowledgeBaseAdapterFactory.getAdapter(extractAdapterType(ragConfig),
                    ragConfig);

            RetrievalDTO.ResultVO result = adapter.retrievalTest(req);
            log.info("Recall test successful: total={}", result != null ? result.getTotal() : 0);
            return result;
        } catch (Exception e) {
            log.error("Recall test failed: {}", e.getMessage(), e);
            String errorMessage = e.getMessage() != null ? e.getMessage() : "null";
            if (e instanceof RenException) {
                throw (RenException) e;
            }
            throw new RenException(ErrorCode.RAG_API_ERROR, errorMessage);
        } finally {
            log.info("=== Recall test operation ended ===");
        }
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void deleteDocumentsByDatasetId(String datasetId) {
        log.info("Cascade clean dataset documents: datasetId={}", datasetId);
        List<DocumentEntity> list = documentDao
                .selectList(new QueryWrapper<DocumentEntity>().eq("dataset_id", datasetId));
        if (list == null || list.isEmpty())
            return;

        List<String> docIds = list.stream().map(DocumentEntity::getDocumentId).toList();

        // Package call existingDeleteLogic (include RAG PhysicalDelete)
        DocumentDTO.BatchIdReq req = DocumentDTO.BatchIdReq.builder().ids(docIds).build();
        this.deleteDocuments(datasetId, req);
    }

    @Override
    public int syncDocumentsFromRAG(String datasetId) {
        log.info("=== Start full sync from RAGFlow documents to local shadow table: datasetId={} ===", datasetId);

        // 1. Get adapter
        Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);
        KnowledgeBaseAdapter adapter = KnowledgeBaseAdapterFactory.getAdapter(extractAdapterType(ragConfig), ragConfig);

        // 2. PaginationPull all remoteDocument
        List<KnowledgeFilesDTO> allRemoteDocs = new ArrayList<>();
        int pageNum = 1;
        int pageSize = 100;
        long totalRemote = Long.MAX_VALUE;

        while ((long) (pageNum - 1) * pageSize < totalRemote) {
            DocumentDTO.ListReq req = DocumentDTO.ListReq.builder()
                    .page(pageNum)
                    .pageSize(pageSize)
                    .build();
            PageData<KnowledgeFilesDTO> remotePage = adapter.getDocumentList(datasetId, req);
            if (remotePage == null || remotePage.getList() == null || remotePage.getList().isEmpty()) {
                break;
            }
            allRemoteDocs.addAll(remotePage.getList());
            totalRemote = remotePage.getTotal();
            pageNum++;
        }

        // 3. Get local existingDocument
        List<DocumentEntity> localDocs = documentDao.selectList(
                new QueryWrapper<DocumentEntity>().eq("dataset_id", datasetId));
        Set<String> localDocIds = localDocs.stream()
                .map(DocumentEntity::getDocumentId)
                .collect(Collectors.toSet());

        // 4. RemoteDocument IDCollection
        Set<String> remoteDocIds = allRemoteDocs.stream()
                .map(KnowledgeFilesDTO::getDocumentId)
                .filter(id -> id != null)
                .collect(Collectors.toSet());

        // 5. Supplement: Insert records existing remotely but missing locallyDocument
        List<KnowledgeFilesDTO> newDocs = allRemoteDocs.stream()
                .filter(doc -> doc.getDocumentId() != null && !localDocIds.contains(doc.getDocumentId()))
                .collect(Collectors.toList());

        int syncCount = 0;
        if (!newDocs.isEmpty()) {
            for (KnowledgeFilesDTO doc : newDocs) {
                try {
                    self.saveDocumentShadow(datasetId, doc, doc.getName(), doc.getChunkMethod(), doc.getParserConfig());
                    // Sync existing remote token/chunk Stats
                    Long tokenCount = doc.getTokenCount() != null ? doc.getTokenCount() : 0L;
                    long chunkCount = doc.getChunkCount() != null ? doc.getChunkCount().longValue() : 0L;
                    if (tokenCount > 0 || chunkCount > 0) {
                        knowledgeBaseService.updateStatistics(datasetId, 0, chunkCount, tokenCount);
                    }
                    syncCount++;
                } catch (Exception e) {
                    log.warn("Failed to sync single document shadow record: docId={}, error={}", doc.getDocumentId(), e.getMessage());
                }
            }
            log.info("Synced {} new document shadow records from RAGFlow, datasetId={}", syncCount, datasetId);
        }

        // 6. Clean: DeleteShadow records not existing remotely but still kept locally
        List<DocumentEntity> deletedDocs = localDocs.stream()
                .filter(entity -> !remoteDocIds.contains(entity.getDocumentId()))
                .collect(Collectors.toList());

        if (!deletedDocs.isEmpty()) {
            List<String> deletedDocIds = new ArrayList<>();
            long totalChunkDelta = 0;
            long totalTokenDelta = 0;

            for (DocumentEntity entity : deletedDocs) {
                deletedDocIds.add(entity.getDocumentId());
                totalChunkDelta += entity.getChunkCount() != null ? entity.getChunkCount() : 0L;
                totalTokenDelta += entity.getTokenCount() != null ? entity.getTokenCount() : 0L;
            }
            try {
                self.deleteDocumentShadows(deletedDocIds, datasetId, totalChunkDelta, totalTokenDelta);
                log.info("Clean up remotely deleted shadow records: {} records, datasetId={}", deletedDocs.size(), datasetId);
            } catch (Exception e) {
                log.warn("Failed to clean shadow records deleted remotely: datasetId={}, error={}", datasetId, e.getMessage());
            }
        }

        // 7. Full Update: Exists both remote and localDocument, sync all fields based on remote
        // Process RAGFlow Reuse documentId After reupload, remote editMetadataChange scenarios
        Map<String, KnowledgeFilesDTO> remoteDocMap = allRemoteDocs.stream()
                .filter(doc -> doc.getDocumentId() != null)
                .collect(Collectors.toMap(KnowledgeFilesDTO::getDocumentId, doc -> doc, (a, b) -> b));

        Map<String, DocumentEntity> localDocMap = localDocs.stream()
                .collect(Collectors.toMap(DocumentEntity::getDocumentId, e -> e, (a, b) -> b));

        int updateCount = 0;
        for (Map.Entry<String, KnowledgeFilesDTO> entry : remoteDocMap.entrySet()) {
            String docId = entry.getKey();
            DocumentEntity local = localDocMap.get(docId);
            if (local == null) {
                continue; // Not local, by step5Process
            }
            KnowledgeFilesDTO remote = entry.getValue();

            // Full field update (remote as source), ensure local and RAGFlow Completely Same
            UpdateWrapper<DocumentEntity> updateWrapper = new UpdateWrapper<DocumentEntity>()
                    .set("run", remote.getRun())
                    .set("status", remote.getStatus() != null ? remote.getStatus() : local.getStatus())
                    .set("progress", remote.getProgress())
                    .set("chunk_count", remote.getChunkCount())
                    .set("token_count", remote.getTokenCount())
                    .set("size", remote.getFileSize())
                    .set("error", remote.getError())
                    .set("process_duration", remote.getProcessDuration())
                    .set("updated_at", new Date())
                    .set("last_sync_at", new Date())
                    .eq("document_id", docId)
                    .eq("dataset_id", datasetId);

            if (remote.getName() != null) {
                updateWrapper.set("name", remote.getName());
            }
            if (remote.getThumbnail() != null) {
                updateWrapper.set("thumbnail", remote.getThumbnail());
            }
            if (remote.getMetaFields() != null) {
                try {
                    updateWrapper.set("meta_fields", objectMapper.writeValueAsString(remote.getMetaFields()));
                } catch (Exception e) {
                    log.warn("Failed to serialize sync update metadata: docId={}, error={}", docId, e.getMessage());
                }
            }

            documentDao.update(null, updateWrapper);

            // Sync stats diff (chunk/token Correct parent table when count changes)
            Long remoteTokenCount = remote.getTokenCount() != null ? remote.getTokenCount() : 0L;
            Long localTokenCount = local.getTokenCount() != null ? local.getTokenCount() : 0L;
            long remoteChunkCount = remote.getChunkCount() != null ? remote.getChunkCount().longValue() : 0L;
            long localChunkCount = local.getChunkCount() != null ? local.getChunkCount().longValue() : 0L;
            long tokenDelta = remoteTokenCount - localTokenCount;
            long chunkDelta = remoteChunkCount - localChunkCount;
            if (tokenDelta != 0 || chunkDelta != 0) {
                knowledgeBaseService.updateStatistics(datasetId, 0, chunkDelta, tokenDelta);
                log.info("Shadow update: fixed knowledge base stats, docId={}, chunkDelta={}, tokenDelta={}", docId, chunkDelta, tokenDelta);
            }

            updateCount++;
        }

        if (syncCount == 0 && deletedDocs.isEmpty() && updateCount == 0) {
            log.info("Local shadow table fully synced with RAGFlow, datasetId={}", datasetId);
        } else {
            log.info("Sync complete: added={}, cleaned={}, updated={}, datasetId={}", syncCount, deletedDocs.size(), updateCount, datasetId);
        }

        return syncCount;
    }

    @Override
    public void syncRunningDocuments() {
        // 1. Query All RUNNING StatusofDocument
        List<DocumentEntity> runningDocs = documentDao.selectList(
                new QueryWrapper<DocumentEntity>()
                        .eq("run", "RUNNING")
                        .eq("status", "1") // Sync onlyEnableofDocument
        );

        if (runningDocs == null || runningDocs.isEmpty()) {
            return;
        }

        log.info("Scheduled task: found {} documents parsing, start sync...", runningDocs.size());

        // 2. by DatasetID Group, reuse Adapter
        Map<String, List<DocumentEntity>> groupedDocs = runningDocs.stream()
                .collect(Collectors.groupingBy(DocumentEntity::getDatasetId));

        groupedDocs.forEach((datasetId, docs) -> {
            KnowledgeBaseAdapter adapter = null;
            try {
                // Initialize Adapter (Each dataset initialized only once)
                Map<String, Object> ragConfig = knowledgeBaseService.getRAGConfigByDatasetId(datasetId);
                adapter = KnowledgeBaseAdapterFactory.getAdapter(extractAdapterType(ragConfig), ragConfig);
            } catch (Exception e) {
                log.warn("Cannot initialize adapter for dataset {}, skip sync: {}", datasetId, e.getMessage());
                return;
            }

            for (DocumentEntity doc : docs) {
                try {
                    // Build Temporary DTO Pass to sync method
                    KnowledgeFilesDTO dto = convertEntityToDTO(doc);
                    // Record before sync Token number
                    Long oldTokenCount = dto.getTokenCount() != null ? dto.getTokenCount() : 0L;

                    syncDocumentStatusWithRAG(dto, adapter);

                    // 3. [Key Fix] Calculate increment andUpdate knowledge baseStats
                    Long newTokenCount = dto.getTokenCount() != null ? dto.getTokenCount() : 0L;
                    Long tokenDelta = newTokenCount - oldTokenCount;

                    // Only whenStatusBecome SUCCESS and Token Update stats when count changes
                    if (tokenDelta != 0) {
                        knowledgeBaseService.updateStatistics(datasetId, 0, 0L, tokenDelta);
                        log.info("Scheduled task: sync fix knowledge base stats, docId={}, tokenDelta={}", dto.getDocumentId(), tokenDelta);
                    }
                } catch (Exception e) {
                    log.error("Sync document {} failed: {}", doc.getDocumentId(), e.getMessage());
                }
            }
        });
    }
}