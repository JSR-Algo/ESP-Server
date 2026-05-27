package tbot.modules.knowledge.rag;

import java.util.List;
import java.util.Map;

import tbot.modules.knowledge.dto.dataset.DatasetDTO;

import tbot.common.page.PageData;
import tbot.modules.knowledge.dto.KnowledgeFilesDTO;
import tbot.modules.knowledge.dto.document.DocumentDTO;
import tbot.modules.knowledge.dto.document.ChunkDTO;
import tbot.modules.knowledge.dto.document.RetrievalDTO;
import java.util.function.Consumer;

/**
 * Knowledge baseAPIAdapter abstract base class
 * Define common knowledge base operation interface, support multiple backendsAPIImplement
 */
public abstract class KnowledgeBaseAdapter {

        /**
         * Get adapter type ID
         * 
         * @return Adapter type (such as:ragflow, milvus, pineconeetc.)
         */
        public abstract String getAdapterType();

        /**
         * Init adapter config
         * 
         * @param config Config Parameters
         */
        public abstract void initialize(Map<String, Object> config);

        /**
         * Validate config valid
         * 
         * @param config Config Parameters
         * @return Validation Result
         */
        public abstract boolean validateConfig(Map<String, Object> config);

        /**
         * Page query document list
         * 
         * @param datasetId   Knowledge baseID
         * @param queryParams Query parameters
         * @param page        Page number
         * @param limit       Items Per Page
         * @return Paged Data
         */
        public abstract PageData<KnowledgeFilesDTO> getDocumentList(String datasetId,
                        DocumentDTO.ListReq req);

        /**
         * According to DocumentIDGet document details
         * 
         * @param datasetId  Knowledge baseID
         * @param documentId DocumentID
         * @return Document Details (Strong type InfoVO)
         */
        public abstract DocumentDTO.InfoVO getDocumentById(String datasetId, String documentId);

        /**
         * Upload document to knowledge base
         * 
         * @param req Upload request parameters
         * @return Uploaded document info
         */
        public abstract KnowledgeFilesDTO uploadDocument(DocumentDTO.UploadReq req);

        /**
         * Query document list by status with pagination
         * 
         * @param datasetId Knowledge baseID
         * @param status    Document parse status
         * @param page      Page number
         * @param limit     Items Per Page
         * @return Paged Data
         */
        public abstract PageData<KnowledgeFilesDTO> getDocumentListByStatus(String datasetId,
                        Integer status,
                        Integer page,
                        Integer limit);

        /**
         * Delete Document (Support batch delete)
         * 
         * @param datasetId Knowledge baseID
         * @param req       Contains DocumentIDList request object
         */
        public abstract void deleteDocument(String datasetId, DocumentDTO.BatchIdReq req);

        /**
         * Parse document (chunking)
         * 
         * @param datasetId   Knowledge baseID
         * @param documentIds DocumentIDList
         * @return Parse Result
         */
        public abstract boolean parseDocuments(String datasetId, List<String> documentIds);

        /**
         * List slices of specified document
         * 
         * @param datasetId  Knowledge baseID
         * @param documentId DocumentID
         * @param req        List request parameters (Pagination, keywords, etc.)
         * @return Chunk listVO
         */
        public abstract ChunkDTO.ListVO listChunks(String datasetId,
                        String documentId,
                        ChunkDTO.ListReq req);

        /**
         * Recall test - Retrieve related slices from knowledge base
         * 
         * @param req Retrieval test request params
         * @return Recall test result
         */
        public abstract RetrievalDTO.ResultVO retrievalTest(
                        RetrievalDTO.TestReq req);

        /**
         * Test Connection
         * 
         * @return Connection test result
         */
        public abstract boolean testConnection();

        /**
         * Get adapter status info
         * 
         * @return Status Info
         */
        public abstract Map<String, Object> getStatus();

        /**
         * Get supported config parameters
         * 
         * @return Config parameter description
         */
        public abstract Map<String, Object> getSupportedConfig();

        /**
         * Get default config
         * 
         * @return Default Config
         */
        public abstract Map<String, Object> getDefaultConfig();

        /**
         * Create dataset
         * 
         * @param req Create Parameters
         * @return Dataset details
         */
        public abstract DatasetDTO.InfoVO createDataset(DatasetDTO.CreateReq req);

        /**
         * Update dataset
         * 
         * @param datasetId DatasetID
         * @param req       Update parameters
         * @return Dataset details
         */
        public abstract DatasetDTO.InfoVO updateDataset(String datasetId, DatasetDTO.UpdateReq req);

        /**
         * Delete dataset
         * 
         * @param req Delete request parameters (includeIDList)
         * @return Batch operation result
         */
        public abstract DatasetDTO.BatchOperationVO deleteDataset(DatasetDTO.BatchIdReq req);

        /**
         * Get dataset document count
         *
         * @param datasetId DatasetID
         * @return Document count
         */
        public abstract Integer getDocumentCount(String datasetId);

        /**
         * Get dataset complete information (name, intro, document count, etc.)
         * For Detection RAGFlow whether remote end deleted, sync name/Intro Change
         *
         * @param datasetId DatasetID
         * @return Dataset details, if RAGFlow Return if end not exist null
         */
        public abstract DatasetDTO.InfoVO getDatasetInfo(String datasetId);

        /**
         * Send streaming request (SSE)
         * 
         * @param endpoint APIEndpoint
         * @param body     Request body
         * @param onData   Data Callback
         */
        public abstract void postStream(String endpoint, Object body, Consumer<String> onData);

        /**
         * SearchBot Ask
         *
         * @param config RAGConfig
         * @param body   Request body
         * @param onData Data Callback
         * @return Response Object
         */
        public abstract Object postSearchBotAsk(Map<String, Object> config, Object body,
                        Consumer<String> onData);

        /**
         * AgentBot Conversation
         *
         * @param config  RAGConfig
         * @param agentId Agent ID
         * @param body    Request body
         * @param onData  Data Callback
         */
        public abstract void postAgentBotCompletion(Map<String, Object> config, String agentId, Object body,
                        Consumer<String> onData);
}