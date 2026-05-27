package tbot.modules.knowledge.dto;

import java.io.Serial;
import java.io.Serializable;
import java.util.Date;
import java.util.Map;

import io.swagger.v3.oas.annotations.media.Schema;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import lombok.Data;

@Data
@Schema(description = "Knowledge base document")
@JsonIgnoreProperties(ignoreUnknown = true)
public class KnowledgeFilesDTO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;
    @Schema(description = "Unique identifier")
    private String id;

    @Schema(description = "Document ID")
    private String documentId;

    @Schema(description = "Knowledge base ID")
    private String datasetId;

    @Schema(description = "Document name")
    private String name;

    @Schema(description = "Document type")
    private String fileType;

    @Schema(description = "File size (bytes)")
    private Long fileSize;

    @Schema(description = "File path")
    private String filePath;

    @Schema(description = "Parse progress (0.0 ~ 1.0)")
    private Double progress;

    @Schema(description = "Thumbnail (Base64 or URL)")
    private String thumbnail;

    @Schema(description = "Parsing duration (unit: seconds)")
    private Double processDuration;

    @Schema(description = "Source type (local, s3, url, etc.)")
    private String sourceType;

    @Schema(description = "Metadata fields (Map format)")
    private Map<String, Object> metaFields;

    @Schema(description = "Chunking method")
    private String chunkMethod;

    @Schema(description = "Parser config")
    private Map<String, Object> parserConfig;

    @Schema(description = "Available status (1: enabled/normal, 0: disabled/invalid)")
    private String status;

    @Schema(description = "Run status (UNSTART/RUNNING/CANCEL/DONE/FAIL)")
    private String run;

    @Schema(description = "Creator")
    private Long creator;

    @Schema(description = "Creation time")
    private Date createdAt;

    @Schema(description = "Updater")
    private Long updater;

    @Schema(description = "Update time")
    private Date updatedAt;

    @Schema(description = "Chunk count")
    private Integer chunkCount;

    @Schema(description = "Token count")
    private Long tokenCount;

    @Schema(description = "Parse error info")
    private String error;

    // DocumentParseStatusConstant Definition
    private static final Integer STATUS_UNSTART = 0;
    private static final Integer STATUS_RUNNING = 1;
    private static final Integer STATUS_CANCEL = 2;
    private static final Integer STATUS_DONE = 3;
    private static final Integer STATUS_FAIL = 4;

    /**
     * GetDocumentParseStatuscode (based onrunField conversion)
     */
    public Integer getParseStatusCode() {
        if (run == null) {
            return STATUS_UNSTART;
        }

        // RAGFlowBased onrunfield value maps directly to correspondingStatuscode
        switch (run.toUpperCase()) {
            case "RUNNING":
                return STATUS_RUNNING;
            case "CANCEL":
                return STATUS_CANCEL;
            case "DONE":
                return STATUS_DONE;
            case "FAIL":
                return STATUS_FAIL;
            case "UNSTART":
            default:
                return STATUS_UNSTART;
        }
    }

}