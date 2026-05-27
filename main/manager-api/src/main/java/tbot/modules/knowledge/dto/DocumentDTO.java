package tbot.modules.knowledge.dto;

import java.io.Serializable;
import java.util.Date;
import java.util.Map;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Document DTO
 */
@Data
@Schema(description = "Knowledge base document")
public class DocumentDTO implements Serializable {
    private static final long serialVersionUID = 1L;

    @Schema(description = "Local ID")
    private String id;

    @Schema(description = "Knowledge base ID")
    private String datasetId;

    @Schema(description = "RAGFlow document ID")
    private String documentId;

    @Schema(description = "Document name")
    private String name;

    @Schema(description = "File size")
    private Long size;

    @Schema(description = "File type")
    private String type;

    @Schema(description = "Chunking method")
    private String chunkMethod;

    @Schema(description = "Parse config")
    private Map<String, Object> parserConfig;

    @Schema(description = "Processing status (1: parsing 3: success 4: failed)")
    private Integer status;

    @Schema(description = "Error info")
    private String error;

    @Schema(description = "Chunk count")
    private Integer chunkCount;

    @Schema(description = "Token count")
    private Long tokenCount;

    @Schema(description = "Enabled")
    private Integer enabled;

    @Schema(description = "Creation time")
    private Date createdAt;

    @Schema(description = "Update time")
    private Date updatedAt;

    @Schema(description = "Upload progress (virtual field)")
    private Double progress;

    @Schema(description = "Thumbnail/preview image (virtual field)")
    private String thumbnail;
}
