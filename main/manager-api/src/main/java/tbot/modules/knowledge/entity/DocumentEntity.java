package tbot.modules.knowledge.entity;

import java.io.Serializable;
import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * Documenttable (Shadow DB for RAGFlow Documents)
 * Corresponding Table Name: ai_knowledge_document
 */
@Data
@TableName(value = "ai_rag_knowledge_document", autoResultMap = true)
@Schema(description = "Knowledge base document table")
public class DocumentEntity implements Serializable {
    private static final long serialVersionUID = 1L;

    @TableId(type = IdType.ASSIGN_UUID)
    @Schema(description = "Local unique ID")
    private String id;

    @Schema(description = "Knowledge base ID (associated with ai_rag_dataset.dataset_id)")
    private String datasetId;

    @Schema(description = "RAGFlow document ID (remote ID)")
    private String documentId;

    @Schema(description = "Document name")
    private String name;

    @Schema(description = "File size (Bytes)")
    private Long size;

    @Schema(description = "File type(pdf/doc/txt, etc.)")
    private String type;

    @Schema(description = "Chunking method")
    private String chunkMethod;

    @Schema(description = "Parse config (JSON String)")
    private String parserConfig;

    @Schema(description = "Available status (1: enabled/normal, 0: disabled/invalid)")
    private String status;

    @Schema(description = "Run status (UNSTART/RUNNING/CANCEL/DONE/FAIL)")
    private String run;

    @Schema(description = "Parse progress (0.0 ~ 1.0)")
    private Double progress;

    @Schema(description = "Thumbnail (Base64 or URL)")
    private String thumbnail;

    @Schema(description = "Parsing duration (unit: seconds)")
    private Double processDuration;

    @Schema(description = "Custom metadata (JSON format)")
    private String metaFields;

    @Schema(description = "Source type (local, s3, url, etc.)")
    private String sourceType;

    @Schema(description = "Parse error info")
    private String error;

    @Schema(description = "Chunk count")
    private Integer chunkCount;

    @Schema(description = "Token count")
    private Long tokenCount;

    @Schema(description = "Whether enabled (0: disabled 1: enabled)")
    private Integer enabled;

    @Schema(description = "Creator")
    @TableField(fill = FieldFill.INSERT)
    private Long creator;

    @Schema(description = "Creation time")
    @TableField(fill = FieldFill.INSERT)
    private Date createdAt;

    @Schema(description = "Update time")
    @TableField(fill = FieldFill.UPDATE)
    private Date updatedAt;

    @Schema(description = "Latest sync time")
    private Date lastSyncAt;
}
