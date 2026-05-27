package tbot.modules.knowledge.dto.document;

import lombok.*;
import io.swagger.v3.oas.annotations.media.Schema;
import java.io.Serializable;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.*;

/**
 * Document management aggregate DTO
 */
@Schema(description = "Document management aggregate DTO")
@JsonIgnoreProperties(ignoreUnknown = true)
public class DocumentDTO {

    /**
     * Upload document request parameters
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Upload document request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class UploadReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Knowledge base ID (owner required)", requiredMode = Schema.RequiredMode.REQUIRED)
        @JsonProperty("dataset_id")
        @NotBlank(message = "Knowledge base ID cannot be empty")
        private String datasetId;

        @Schema(description = "Filename (overrides original filename if specified)")
        private String name;

        @Schema(description = "Chunking method")
        @JsonProperty("chunk_method")
        private DocumentDTO.InfoVO.ChunkMethod chunkMethod;

        @Schema(description = "Parse parameter config")
        @JsonProperty("parser_config")
        private DocumentDTO.InfoVO.ParserConfig parserConfig;

        @Schema(description = "Virtual folder path (default /)")
        @JsonProperty("parent_path")
        private String parentPath;

        @Schema(description = "Metadata field")
        @JsonProperty("meta")
        private Map<String, Object> metaFields;

        @Schema(description = "File binary stream (supports PDF, DOCX, TXT, MD, and other formats)", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotNull(message = "Upload file cannot be empty")
        private org.springframework.web.multipart.MultipartFile file;
    }

    /**
     * Update document request parameters
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Update document request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class UpdateReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "New document name (must include file suffix, original type cannot be changed)")
        private String name;

        @Schema(description = "Enabled/disabled status (true: enabled, false: disabled; disabled means excluded from retrieval)")
        private Boolean enabled;

        @Schema(description = "New parsing method (changing this resets parsing status)")
        @JsonProperty("chunk_method")
        private InfoVO.ChunkMethod chunkMethod;

        @Schema(description = "New parser detailed config (should be used with chunk_method)")
        @JsonProperty("parser_config")
        private InfoVO.ParserConfig parserConfig;
    }

    /**
     * Get document list request parameters
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Get document list request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ListReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Page number (default: 1)")
        private Integer page;

        @Schema(description = "Items per page (default: 30)")
        @JsonProperty("page_size")
        private Integer pageSize;

        @Schema(description = "Sort field (optional: create_time, name, size; default: create_time)")
        private String orderby;

        @Schema(description = "Whether to sort descending (true: newest/largest first; false: oldest/smallest first; default: true)")
        private Boolean desc;

        @Schema(description = "Exact filter: document ID")
        private String id;

        @Schema(description = "Exact filter: full document name (with suffix)")
        private String name;

        @Schema(description = "Fuzzy search: document name keyword")
        private String keywords;

        @Schema(description = "Filter: File suffix list (such as ['pdf', 'docx'])")
        private List<String> suffix;

        @Schema(description = "Filter: RunStatusList")
        private List<InfoVO.RunStatus> run;

        @Schema(description = "Filter: StartCreation time (Timestamp, Millisecond)")
        @JsonProperty("create_time_from")
        private Long createTimeFrom;

        @Schema(description = "Filter: EndCreation time (Timestamp, Millisecond)")
        @JsonProperty("create_time_to")
        private Long createTimeTo;
    }

    /**
     * BatchDocumentOperation request parameters (Used forDeleteparse, etc.)
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "BatchDocumentOperation request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class BatchIdReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Document ID List", requiredMode = Schema.RequiredMode.REQUIRED)
        @JsonProperty("ids") // For compatibility, could also support document_ids, but use ids uniformly here
        @JsonAlias("document_ids")
        @NotEmpty(message = "Document ID list cannot be empty")
        private List<String> ids;
    }

    /**
     * Knowledge base documentInfo VO
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Knowledge base documentInfo")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class InfoVO implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Document ID (Unique identifier)", requiredMode = Schema.RequiredMode.REQUIRED)
        private String id;

        @Schema(description = "DocumentThumbnail URL (Base64 or Link)")
        private String thumbnail;

        @Schema(description = "Owning knowledge base ID", requiredMode = Schema.RequiredMode.REQUIRED)
        @JsonProperty("dataset_id")
        private String datasetId;

        @Schema(description = "DocumentParse Method (DeterminesDocumentHowSliced)")
        @JsonProperty("chunk_method")
        private ChunkMethod chunkMethod;

        @Schema(description = "Associated ETL Pipeline ID (If any)")
        @JsonProperty("pipeline_id")
        private String pipelineId;

        @Schema(description = "DocumentParser detailed config")
        @JsonProperty("parser_config")
        private ParserConfig parserConfig;

        @Schema(description = "Source type (such as local, s3, url etc)")
        @JsonProperty("source_type")
        private String sourceType;

        @Schema(description = "DocumentFile type (such as pdf, docx, txt)", requiredMode = Schema.RequiredMode.REQUIRED)
        private String type;

        @Schema(description = "CreatorUser ID")
        @JsonProperty("created_by")
        private String createdBy;

        @Schema(description = "Document name (Include extension)", requiredMode = Schema.RequiredMode.REQUIRED)
        private String name;

        @Schema(description = "File storage path or location identifier")
        private String location;

        @Schema(description = "File size (Unit: Bytes)")
        private Long size;

        @Schema(description = "Included Total tokens (Stats after parsing)")
        @JsonProperty("token_count")
        private Long tokenCount;

        @Schema(description = "Included chunks (Chunk) Total count")
        @JsonProperty("chunk_count")
        private Long chunkCount;

        @Schema(description = "Parse Progress (0.0 ~ 1.0, 1.0 Indicate Complete)")
        private Double progress;

        @Schema(description = "Current ProgressDescriptionorError info")
        @JsonProperty("progress_msg")
        private String progressMsg;

        @Schema(description = "Start processingTimestamp (RAGFlowReturnRFC1123Format)")
        @JsonProperty("process_begin_at")
        private String processBeginAt;

        @Schema(description = "Total processing time (Unit: seconds)")
        @JsonProperty("process_duration")
        private Double processDuration;

        @Schema(description = "CustomMetadata field (Key-Value Key-value pairs)")
        @JsonProperty("meta_fields")
        private Map<String, Object> metaFields;

        @Schema(description = "File suffix (No dot)")
        private String suffix;

        @Schema(description = "DocumentParse RunningStatus")
        private RunStatus run;

        @Schema(description = "DocumentAvailable status (1: enabled/normal, 0: disabled/invalid)", requiredMode = Schema.RequiredMode.REQUIRED)
        private String status;

        @Schema(description = "Creation time (Timestamp, Millisecond)", requiredMode = Schema.RequiredMode.REQUIRED)
        @JsonProperty("create_time")
        private Long createTime;

        @Schema(description = "Creation date (RAGFlowReturnRFC1123Format)")
        @JsonProperty("create_date")
        private String createDate;

        @Schema(description = "LastUpdate time (Timestamp, Millisecond)")
        @JsonProperty("update_time")
        private Long updateTime;

        @Schema(description = "LastUpdate date (RAGFlowReturnRFC1123Format)")
        @JsonProperty("update_date")
        private String updateDate;

        /**
         * Parse method enum (ChunkMethod)
         */
        public enum ChunkMethod {
            @Schema(description = "General Mode: Suitable for most plain text or mixedDocument")
            @JsonProperty("naive")
            NAIVE,
            @Schema(description = "Manual Mode: Allow users to manually edit slices")
            @JsonProperty("manual")
            MANUAL,
            @Schema(description = "Q&A Mode: Specially Optimized Q&A Format ofDocument")
            @JsonProperty("qa")
            QA,
            @Schema(description = "Table Mode: Specially Optimized Excel or CSV And table data")
            @JsonProperty("table")
            TABLE,
            @Schema(description = "Paper Mode: Optimized for academic paper layout")
            @JsonProperty("paper")
            PAPER,
            @Schema(description = "Book Mode: Optimized for book chapter structure")
            @JsonProperty("book")
            BOOK,
            @Schema(description = "Laws and regulations mode: Optimized for legal article structure")
            @JsonProperty("laws")
            LAWS,
            @Schema(description = "Presentation mode: For PPT Demo file optimization")
            @JsonProperty("presentation")
            PRESENTATION,
            @Schema(description = "Image Mode: For ImagesContentPerform OCR andDescription")
            @JsonProperty("picture")
            PICTURE,
            @Schema(description = "Overall Mode: WholeDocumentAs slice")
            @JsonProperty("one")
            ONE,
            @Schema(description = "Knowledge graph mode: Extract entity relations to build graph")
            @JsonProperty("knowledge_graph")
            KNOWLEDGE_GRAPH,
            @Schema(description = "Email Mode: Optimized for email format")
            @JsonProperty("email")
            EMAIL;
        }

        /**
         * RunStatusEnum (RunStatus)
         */
        public enum RunStatus {
            @Schema(description = "Not started: Wait parse queue")
            @JsonProperty("UNSTART")
            UNSTART,
            @Schema(description = "In progress: Parsing or indexing")
            @JsonProperty("RUNNING")
            RUNNING,
            @Schema(description = "Canceled: User manually canceled")
            @JsonProperty("CANCEL")
            CANCEL,
            @Schema(description = "Completed: Parse Successful")
            @JsonProperty("DONE")
            DONE,
            @Schema(description = "Fail: Error during parsing")
            @JsonProperty("FAIL")
            FAIL;
        }

        /**
         * Layout recognition model enum
         */
        public enum LayoutRecognize {
            @Schema(description = "DepthDocumentUnderstanding Model: Suitable complex layout")
            @JsonProperty("DeepDOC")
            DeepDOC,
            @Schema(description = "Simple rule model: Suitable for plain text")
            @JsonProperty("Simple")
            Simple;
        }

        @Data
        @Builder
        @NoArgsConstructor
        @AllArgsConstructor
        @Schema(description = "DocumentParser parameter config")
        @JsonIgnoreProperties(ignoreUnknown = true)
        public static class ParserConfig implements Serializable {
            private static final long serialVersionUID = 1L;

            @Schema(description = "SliceMax Token count (Suggested value: 512, 1024, 2048)")
            @JsonProperty("chunk_token_num")
            private Integer chunkTokenNum;

            @Schema(description = "SegmentSeparator (Support escape characters, such as \\n)")
            private String delimiter;

            @Schema(description = "Layout recognition model (DeepDOC/Simple)")
            @JsonProperty("layout_recognize")
            private LayoutRecognize layoutRecognize;

            @Schema(description = "Whether to Excel Convert to HTML Table")
            @JsonProperty("html4excel")
            private Boolean html4excel;

            @Schema(description = "Auto extract keywordsQuantity (0 Indicates no extraction)")
            @JsonProperty("auto_keywords")
            private Integer autoKeywords;

            @Schema(description = "Auto-generate questionsQuantity (0 Indicates no generation)")
            @JsonProperty("auto_questions")
            private Integer autoQuestions;

            @Schema(description = "Auto-generate tagsQuantity")
            @JsonProperty("topn_tags")
            private Integer topnTags;

            @Schema(description = "RAPTOR Advanced index config")
            private RaptorConfig raptor;

            @Schema(description = "GraphRAG Knowledge graph config")
            @JsonProperty("graphrag")
            private GraphRagConfig graphRag;

            @Data
            @Builder
            @NoArgsConstructor
            @AllArgsConstructor
            @Schema(description = "RAPTOR (Recursive summary index) Config")
            @JsonIgnoreProperties(ignoreUnknown = true)
            public static class RaptorConfig implements Serializable {
                private static final long serialVersionUID = 1L;
                @Schema(description = "Enabled RAPTOR Index")
                @JsonProperty("use_raptor")
                private Boolean useRaptor;
            }

            @Data
            @Builder
            @NoArgsConstructor
            @AllArgsConstructor
            @Schema(description = "GraphRAG (Graph-enhanced retrieval) Config")
            @JsonIgnoreProperties(ignoreUnknown = true)
            public static class GraphRagConfig implements Serializable {
                private static final long serialVersionUID = 1L;
                @Schema(description = "Enabled GraphRAG Index")
                @JsonProperty("use_graphrag")
                private Boolean useGraphRag;
            }
        }
    }
}
