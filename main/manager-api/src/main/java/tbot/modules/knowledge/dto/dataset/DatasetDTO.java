package tbot.modules.knowledge.dto.dataset;

import lombok.*;
import io.swagger.v3.oas.annotations.media.Schema;
import java.io.Serializable;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonInclude;
import jakarta.validation.constraints.*;

/**
 * Knowledge base management aggregate DTO
 * <p>
 * Container class, contains all knowledge base module requests/ResponseStatic inner class definition of object.
 * </p>
 */
@Schema(description = "Knowledge base management aggregate DTO")
@JsonIgnoreProperties(ignoreUnknown = true)
public class DatasetDTO {

    // ========== Common inner class ==========

    /**
     * Parser config
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Parser config")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ParserConfig implements Serializable {

        @Schema(description = "Chunk token count", example = "128")
        @JsonProperty("chunk_token_num")
        private Integer chunkTokenNum;

        @Schema(description = "Separator", example = "\\n!?;.;!?")
        private String delimiter;

        @Schema(description = "Layout recognition model: DeepDOC / Simple", example = "DeepDOC")
        @JsonProperty("layout_recognize")
        private String layoutRecognize;

        @Schema(description = "Whether to convert Excel to HTML", example = "false")
        private Boolean html4excel;

        @Schema(description = "Auto-generated keyword count (0 means off)", example = "0")
        @JsonProperty("auto_keywords")
        private Integer autoKeywords;

        @Schema(description = "Auto-generated question count (0 means off)", example = "0")
        @JsonProperty("auto_questions")
        private Integer autoQuestions;
    }

    // ========== Request class ==========

    /**
     * Create knowledge base request (Mapped API 1: create)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Create knowledge base request")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class CreateReq implements Serializable {

        @NotBlank(message = "Knowledge base name cannot be empty")
        @Schema(description = "Knowledge base name", requiredMode = Schema.RequiredMode.REQUIRED, example = "my_dataset")
        private String name;

        @Schema(description = "Knowledge base avatar (Base64 encoded)", example = "")
        private String avatar;

        @Schema(description = "Knowledge base description", example = "Used to store product documents")
        private String description;

        @Schema(description = "Embedding model name", example = "BAAI/bge-large-zh-v1.5")
        @JsonProperty("embedding_model")
        private String embeddingModel;

        @Schema(description = "Permission setting: me / team", example = "me")
        private String permission;

        @Schema(description = "Chunking method: naive / manual / qa / table / paper / book / laws / presentation / picture / one / knowledge_graph / email", example = "naive")
        @JsonProperty("chunk_method")
        private String chunkMethod;

        @Schema(description = "Parser config")
        @JsonProperty("parser_config")
        private ParserConfig parserConfig;
    }

    /**
     * Update knowledge base request (Mapped API 4: update)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Update knowledge base request")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class UpdateReq implements Serializable {

        @Schema(description = "Knowledge base name", example = "updated_dataset")
        private String name;

        @Schema(description = "Knowledge base avatar (Base64 encoded)", example = "")
        private String avatar;

        @Schema(description = "Knowledge base description", example = "Updated description")
        private String description;

        @Schema(description = "Permission setting: me / team", example = "team")
        private String permission;

        @Schema(description = "Embedding model name", example = "BAAI/bge-large-zh-v1.5")
        @JsonProperty("embedding_model")
        private String embeddingModel;

        @Schema(description = "Chunking method: naive / manual / qa / table / paper / book / laws / presentation / picture / one / knowledge_graph / email", example = "naive")
        @JsonProperty("chunk_method")
        private String chunkMethod;

        @Schema(description = "Parser config")
        @JsonProperty("parser_config")
        private ParserConfig parserConfig;

        @JsonInclude(JsonInclude.Include.NON_NULL)
        @Schema(description = "PageRank weight (0-100)", example = "50")
        private Integer pagerank;
    }

    /**
     * Query knowledge base list request (Mapped API 3: list_datasets)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Query knowledge base list request")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ListReq implements Serializable {

        @Schema(description = "Page number (starts from 1)", example = "1")
        private Integer page;

        @Schema(description = "Items per page", example = "30")
        @JsonProperty("page_size")
        private Integer pageSize;

        @Schema(description = "Sort field: create_time / update_time", example = "create_time")
        private String orderby;

        @Schema(description = "Descending", example = "true")
        private Boolean desc;

        @Schema(description = "Filter by name (fuzzy match)", example = "my_dataset")
        private String name;

        @Schema(description = "Filter by knowledge base ID", example = "abc123")
        private String id;
    }

    /**
     * Batch delete knowledge base request (Mapped API 2: delete)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Batch delete knowledge base request")
    public static class BatchIdReq implements Serializable {

        @NotNull(message = "Knowledge base ID list cannot be empty")
        @Size(min = 1, message = "At least one knowledge base ID required")
        @Schema(description = "Knowledge base ID list", requiredMode = Schema.RequiredMode.REQUIRED, example = "[\"id1\", \"id2\"]")
        private List<String> ids;
    }

    /**
     * Run GraphRAG request
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Run GraphRAG request")
    public static class RunGraphRagReq implements Serializable {

        @Schema(description = "Entity type list", example = "[\"person\", \"organization\"]")
        @JsonProperty("entity_types")
        private List<String> entityTypes;

        @Schema(description = "Build method: light / fast / full", example = "light")
        private String method;
    }

    /**
     * Run RAPTOR request
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Run RAPTOR request")
    public static class RunRaptorReq implements Serializable {

        @Schema(description = "Max cluster count", example = "64")
        @JsonProperty("max_cluster")
        private Integer maxCluster;

        @Schema(description = "Custom prompt", example = "Please summarize following content...")
        private String prompt;
    }

    /**
     * Async task ID response VO (Mapped API 7/8: run_graphrag/run_raptor)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Async task ID response")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class TaskIdVO implements Serializable {

        @Schema(description = "GraphRAG task ID", example = "task_uuid_12345678")
        @JsonProperty("graphrag_task_id")
        private String graphragTaskId;

        @Schema(description = "RAPTOR task ID", example = "task_uuid_87654321")
        @JsonProperty("raptor_task_id")
        private String raptorTaskId;
    }

    // ========== Responseclass ==========

    /**
     * Knowledge base details VO (Mapped API 1/3 returned data item)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Knowledge base details VO")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class InfoVO implements Serializable {

        @Schema(description = "Knowledge base ID", example = "abc123")
        private String id;

        @Schema(description = "Knowledge base name", example = "my_dataset")
        private String name;

        @Schema(description = "Knowledge base avatar (Base64 encoded)", example = "")
        private String avatar;

        @Schema(description = "Tenant ID", example = "tenant_001")
        @JsonProperty("tenant_id")
        private String tenantId;

        @Schema(description = "Knowledge base description", example = "Used to store product documents")
        private String description;

        @Schema(description = "Embedding model name", example = "BAAI/bge-large-zh-v1.5")
        @JsonProperty("embedding_model")
        private String embeddingModel;

        @Schema(description = "Permission setting: me / team", example = "me")
        private String permission;

        @Schema(description = "Chunking method", example = "naive")
        @JsonProperty("chunk_method")
        private String chunkMethod;

        @Schema(description = "Parser config")
        @JsonProperty("parser_config")
        private ParserConfig parserConfig;

        @Schema(description = "Total chunks", example = "1024")
        @JsonProperty("chunk_count")
        private Long chunkCount;

        @Schema(description = "Total documents", example = "50")
        @JsonProperty("document_count")
        private Long documentCount;

        @Schema(description = "Creation time (timestamp)", example = "1700000000000")
        @JsonProperty("create_time")
        private Long createTime;

        @Schema(description = "Update time (timestamp)", example = "1700000001000")
        @JsonProperty("update_time")
        private Long updateTime;

        @Schema(description = "Total Token count", example = "102400")
        @JsonProperty("token_num")
        private Long tokenNum;

        @Schema(description = "Creation date (format: yyyy-MM-dd HH:mm:ss)")
        @JsonProperty("create_date")
        private String createDate;

        @Schema(description = "Last update date (format: yyyy-MM-dd HH:mm:ss)")
        @JsonProperty("update_date")
        private String updateDate;
    }

    /**
     * Batch operation response VO
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Batch operation response VO")
    public static class BatchOperationVO implements Serializable {

        @Schema(description = "Successful operation count", example = "5")
        @JsonProperty("success_count")
        private Integer successCount;

        @Schema(description = "Error list")
        private List<Object> errors;
    }

    // ========== Knowledge graph related ==========

    /**
     * Knowledge graph data VO (Mapped API 5: knowledge_graph)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Knowledge graph data VO")
    public static class GraphVO implements Serializable {

        @Schema(description = "Graph node list")
        private List<Node> nodes;

        @Schema(description = "Graph edge list")
        private List<Edge> edges;

        @Schema(description = "Mind map data")
        @JsonProperty("mind_map")
        private Map<String, Object> mindMap;

        /**
         * Graph node
         */
        @Data
        @NoArgsConstructor
        @AllArgsConstructor
        @Builder
        @Schema(description = "Graph node")
        @JsonIgnoreProperties(ignoreUnknown = true)
        public static class Node implements Serializable {

            @Schema(description = "Node ID", example = "node_001")
            private String id;

            @Schema(description = "Node label", example = "Product")
            private String label;

            @Schema(description = "PageRank value", example = "0.85")
            private Double pagerank;

            @Schema(description = "Node color", example = "#FF5733")
            private String color;

            @Schema(description = "Node image URL", example = "https://example.com/icon.png")
            private String img;
        }

        /**
         * Graph edge
         */
        @Data
        @NoArgsConstructor
        @AllArgsConstructor
        @Builder
        @Schema(description = "Graph edge")
        @JsonIgnoreProperties(ignoreUnknown = true)
        public static class Edge implements Serializable {

            @Schema(description = "Source node ID", example = "node_001")
            private String source;

            @Schema(description = "Target node ID", example = "node_002")
            private String target;

            @Schema(description = "Edge weight", example = "0.75")
            private Double weight;

            @Schema(description = "Edge label (relationship description)", example = "Belongs to")
            private String label;
        }
    }

    // ========== Async task tracking (GraphRAG/RAPTOR) ==========

    /**
     * Async task tracking VO (Mapped API 9/10: Task progress return)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Async task tracking VO")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class TaskTraceVO implements Serializable {

        @Schema(description = "Task ID", example = "task_001")
        private String id;

        @Schema(description = "Document ID", example = "doc_001")
        @JsonProperty("doc_id")
        private String docId;

        @Schema(description = "Start page", example = "1")
        @JsonProperty("from_page")
        private Integer fromPage;

        @Schema(description = "End page", example = "10")
        @JsonProperty("to_page")
        private Integer toPage;

        @Schema(description = "Progress percentage (0.0 - 1.0)", example = "0.75")
        private Double progress;

        @Schema(description = "Progress message", example = "Processing page 5...")
        @JsonProperty("progress_msg")
        private String progressMsg;

        @Schema(description = "Creation time (timestamp)", example = "1700000000000")
        @JsonProperty("create_time")
        private Long createTime;

        @Schema(description = "Update time (timestamp)", example = "1700000001000")
        @JsonProperty("update_time")
        private Long updateTime;
    }
}
