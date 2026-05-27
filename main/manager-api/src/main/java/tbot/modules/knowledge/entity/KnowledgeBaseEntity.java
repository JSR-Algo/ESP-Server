package tbot.modules.knowledge.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

@Data
@TableName(value = "ai_rag_dataset", autoResultMap = true)
@Schema(description = "Knowledge base table")
public class KnowledgeBaseEntity {

    @TableId(type = IdType.ASSIGN_UUID)
    @Schema(description = "Unique identifier")
    private String id;

    @Schema(description = "Knowledge base ID")
    private String datasetId;

//    @Deprecated
    @Schema(description = "RAG model config ID (credential pointer for connecting to RAGFlow)")
    private String ragModelId;

    @Schema(description = "Tenant ID")
    private String tenantId;

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

    @Schema(description = "Total documents")
    private Long documentCount;

    @Schema(description = "Total Token count")
    private Long tokenNum;

    @Schema(description = "Status (0: disabled 1: enabled)")
    private Integer status;

    @Schema(description = "Creator")
    @TableField(fill = FieldFill.INSERT)
    private Long creator;

    @Schema(description = "Creation time")
    @TableField(fill = FieldFill.INSERT)
    private Date createdAt;

    @Schema(description = "Updater")
    @TableField(fill = FieldFill.UPDATE)
    private Long updater;

    @Schema(description = "Update time")
    @TableField(fill = FieldFill.UPDATE)
    private Date updatedAt;
}