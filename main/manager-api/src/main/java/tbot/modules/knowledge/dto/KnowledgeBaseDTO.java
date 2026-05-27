package tbot.modules.knowledge.dto;

import java.io.Serial;
import java.io.Serializable;
import java.util.Date;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@Schema(description = "Knowledge base knowledge base")
public class KnowledgeBaseDTO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    @Schema(description = "Unique identifier")
    private String id;

    @Schema(description = "Knowledge base ID")
    private String datasetId;

    @Schema(description = "RAG model config ID")
    private String ragModelId;

    @Schema(description = "Knowledge base name")
    private String name;

    @Schema(description = "Knowledge base avatar (Base64)")
    private String avatar;

    @Schema(description = "Knowledge base description")
    private String description;

    @Schema(description = "Embedding model name")
    private String embeddingModel;

    @Schema(description = "Permission setting: me/team")
    private String permission;

    @Schema(description = "Chunking method")
    private String chunkMethod;

    @Schema(description = "Parser config(JSON String)")
    private String parserConfig;

    @Schema(description = "Total chunks")
    private Long chunkCount;

    @Schema(description = "Total Token count")
    private Long tokenNum;

    @Schema(description = "Status (0: disabled 1: enabled)")
    private Integer status;

    @Schema(description = "Creator")
    private Long creator;

    @Schema(description = "Creation time")
    private Date createdAt;

    @Schema(description = "Updater")
    private Long updater;

    @Schema(description = "Update time")
    private Date updatedAt;

    @Schema(description = "Document count")
    private Integer documentCount;
}