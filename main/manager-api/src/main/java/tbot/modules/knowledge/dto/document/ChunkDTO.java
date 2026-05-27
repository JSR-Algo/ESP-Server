package tbot.modules.knowledge.dto.document;

import lombok.*;
import io.swagger.v3.oas.annotations.media.Schema;
import java.io.Serializable;
import java.util.List;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import jakarta.validation.constraints.*;

/**
 * Chunk management aggregate DTO
 */
@Schema(description = "Chunk management aggregate DTO")
@JsonIgnoreProperties(ignoreUnknown = true)
public class ChunkDTO {

    /**
     * Add slice request parameters
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Add slice request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class AddReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Chunk content", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotBlank(message = "Slice content cannot be empty")
        private String content;

        @Schema(description = "Important keyword list")
        @JsonProperty("important_keywords")
        private List<String> importantKeywords;

        @Schema(description = "Preset question list")
        private List<String> questions;
    }

    /**
     * Update slice request parameters
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Update slice request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class UpdateReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "New chunk content")
        private String content;

        @Schema(description = "Update keyword list (overwrite existing list)")
        @JsonProperty("important_keywords")
        private List<String> importantKeywords;

        @Schema(description = "Enable/disable (true: enable, false: disable)")
        private Boolean available;
    }

    /**
     * Get chunk list request parameters
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Get chunk list request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ListReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Page number (default 1)")
        private Integer page;

        @Schema(description = "Items per page (default 30)")
        @JsonProperty("page_size")
        private Integer pageSize;

        @Schema(description = "Search keyword (full-text search)")
        private String keywords;

        @Schema(description = "Exact slice ID")
        private String id;
    }

    /**
     * Batch delete chunk request parameters
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Batch delete chunk request parameters")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class RemoveReq implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Slice ID list", requiredMode = Schema.RequiredMode.REQUIRED)
        @JsonProperty("chunk_ids")
        @NotEmpty(message = "Chunk ID list cannot be empty")
        private List<String> chunkIds;
    }

    /**
     * Document chunk info VO
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Document chunk info")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class InfoVO implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Chunk ID (usually document_id + index)", requiredMode = Schema.RequiredMode.REQUIRED)
        private String id;

        @Schema(description = "Chunk text content (main object for full-text retrieval)", requiredMode = Schema.RequiredMode.REQUIRED)
        private String content;

        @Schema(description = "Owning document ID", requiredMode = Schema.RequiredMode.REQUIRED)
        @JsonProperty("document_id")
        private String documentId;

        @Schema(description = "Document name / keyword")
        @JsonProperty("docnm_kwd")
        private String docnmKwd;

        @Schema(description = "Important keyword list (for keyword-enhanced retrieval)")
        @JsonProperty("important_keywords")
        private List<String> importantKeywords;

        @Schema(description = "Preset question list (for Q&A mode enhancement)")
        private List<String> questions;

        @Schema(description = "Associated image ID")
        @JsonProperty("image_id")
        private String imageId;

        @Schema(description = "Owning knowledge base ID")
        @JsonProperty("dataset_id")
        private String datasetId;

        @Schema(description = "Whether chunk is available (true: participates in retrieval, false: disabled)")
        private Boolean available;

        @Schema(description = "Slice position index list in original text (RAGFlow returns nested array, e.g. [[start, end, filename]])")
        private List<List<Object>> positions;

        @Schema(description = "Token ID list")
        @JsonProperty("token")
        private List<Integer> token;
    }

    /**
     * Shard list aggregate response
     */
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Shard list aggregate response")
    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class ListVO implements Serializable {
        private static final long serialVersionUID = 1L;

        @Schema(description = "Chunk info list")
        private List<InfoVO> chunks;

        @Schema(description = "Associated document details")
        private DocumentDTO.InfoVO doc;

        @Schema(description = "Total records")
        private Long total;
    }
}
