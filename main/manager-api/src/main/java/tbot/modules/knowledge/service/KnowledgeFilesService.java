package tbot.modules.knowledge.service;

import java.util.List;
import java.util.Map;

import org.springframework.web.multipart.MultipartFile;

import tbot.common.page.PageData;
import tbot.modules.knowledge.dto.KnowledgeFilesDTO;
import tbot.modules.knowledge.dto.document.ChunkDTO;
import tbot.modules.knowledge.dto.document.RetrievalDTO;
import tbot.modules.knowledge.dto.document.DocumentDTO;

/**
 * Knowledge base document service interface
 */
public interface KnowledgeFilesService {

        /**
         * Page query document list
         * 
         * @param knowledgeFilesDTO Query Conditions
         * @param page              Page number
         * @param limit             Items Per Page
         * @return Paged Data
         */
        PageData<KnowledgeFilesDTO> getPageList(KnowledgeFilesDTO knowledgeFilesDTO, Integer page, Integer limit);

        /**
         * According to DocumentIDand Knowledge BaseIDGet document details
         * 
         * @param documentId DocumentID
         * @param datasetId  Knowledge baseID
         * @return Document Details (Strong type InfoVO)
         */
        DocumentDTO.InfoVO getByDocumentId(String documentId, String datasetId);

        /**
         * Upload document to knowledge base
         * 
         * @param datasetId    Knowledge baseID
         * @param file         Uploaded file
         * @param name         Document name
         * @param metaFields   Metadata field
         * @param chunkMethod  Chunking Method
         * @param parserConfig Parser config
         * @return Uploaded document info
         */
        KnowledgeFilesDTO uploadDocument(String datasetId, MultipartFile file, String name,
                        Map<String, Object> metaFields, String chunkMethod,
                        Map<String, Object> parserConfig);

        /**
         * Batch delete documents
         * 
         * @param datasetId Knowledge baseID
         * @param req       Delete request parameters (Contains docsIDList)
         */
        void deleteDocuments(String datasetId, DocumentDTO.BatchIdReq req);

        /**
         * GetRAGConfig Info
         * 
         * @param ragModelId RAGModel configID
         * @return RAGConfig Info
         */
        Map<String, Object> getRAGConfig(String ragModelId);

        /**
         * Parse document (chunking)
         * 
         * @param datasetId   Knowledge baseID
         * @param documentIds DocumentIDList
         * @return Parse Result
         */
        boolean parseDocuments(String datasetId, List<String> documentIds);

        /**
         * List slices of specified document
         * 
         * @param datasetId  Knowledge baseID
         * @param documentId DocumentID
         * @param req        Chunk list request params
         * @return Slice list info
         */
        ChunkDTO.ListVO listChunks(String datasetId, String documentId, ChunkDTO.ListReq req);

        /**
         * Recall test
         * 
         * @param req Retrieval test request params
         * @return Recall test result
         */
        RetrievalDTO.ResultVO retrievalTest(RetrievalDTO.TestReq req);

        /**
         * Save document shadow record
         */
        boolean saveDocumentShadow(String datasetId, KnowledgeFilesDTO result, String originalName, String chunkMethod,
                        Map<String, Object> parserConfig);

        /**
         * Batch delete document shadow records and sync stats
         * 
         * @param documentIds DocumentIDList
         * @param datasetId   DatasetID
         * @param chunkDelta  Total chunks to deduct
         * @param tokenDelta  Total to deductTokennumber
         */
        void deleteDocumentShadows(List<String> documentIds, String datasetId, Long chunkDelta, Long tokenDelta);

        /**
         * By datasetIDClean all related documents (Cascade delete only)
         * 
         * @param datasetId DatasetID
         */
        void deleteDocumentsByDatasetId(String datasetId);

        /**
         * Sync all in RUNNING Documents by status (For scheduled task call)
         */
        void syncRunningDocuments();

        /**
         * fromRAGFlowFull sync documents to local shadow table
         * Pull all remote documents, compare with local shadow table, insert missing records
         *
         * @param datasetId DatasetID
         * @return New synced document count
         */
        int syncDocumentsFromRAG(String datasetId);
}