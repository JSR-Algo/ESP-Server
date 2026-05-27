package tbot.modules.knowledge.dto.file;

import lombok.*;
import io.swagger.v3.oas.annotations.media.Schema;
import java.io.Serializable;
import java.util.List;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.*;
import org.springframework.web.multipart.MultipartFile;

/**
 * File management aggregate DTO
 * <p>
 * Container class, contains all file module requests/ResponseStatic inner class definition of object.
 * </p>
 */
@Schema(description = "File management aggregate DTO")
public class FileDTO {

    // ========== Request class ==========

    /**
     * File upload request (Corresponding API 1: upload)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "File upload request")
    public static class UploadReq implements Serializable {

        @NotNull(message = "File cannot be empty")
        @Schema(description = "Uploaded file", requiredMode = Schema.RequiredMode.REQUIRED)
        private MultipartFile file;

        @Schema(description = "Parent folder ID (upload to root if empty)", example = "folder_001")
        @JsonProperty("parent_id")
        private String parentId;
    }

    /**
     * Create folder request (Corresponding API 2: create)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Create folder request")
    public static class CreateReq implements Serializable {

        @NotBlank(message = "Folder name cannot be empty")
        @Schema(description = "Folder name", requiredMode = Schema.RequiredMode.REQUIRED, example = "New folder")
        private String name;

        @Schema(description = "Parent folder ID (create in root if empty)", example = "folder_001")
        @JsonProperty("parent_id")
        private String parentId;

        @NotBlank(message = "Type cannot be empty")
        @Schema(description = "Type: FOLDER", requiredMode = Schema.RequiredMode.REQUIRED, example = "FOLDER")
        @Builder.Default
        private String type = "FOLDER";
    }

    /**
     * Rename request (Corresponding API 6: rename)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Rename request")
    public static class RenameReq implements Serializable {

        @NotBlank(message = "File ID cannot be empty")
        @Schema(description = "File/folder ID", requiredMode = Schema.RequiredMode.REQUIRED, example = "file_001")
        @JsonProperty("file_id")
        private String fileId;

        @NotBlank(message = "New name cannot be empty")
        @Schema(description = "New name", requiredMode = Schema.RequiredMode.REQUIRED, example = "Renamed file")
        private String name;
    }

    /**
     * Move request (Corresponding API 7: move)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Move request")
    public static class MoveReq implements Serializable {

        @NotEmpty(message = "Source file ID list cannot be empty")
        @Schema(description = "Source file/folder ID list", requiredMode = Schema.RequiredMode.REQUIRED, example = "[\"file_001\", \"file_002\"]")
        @JsonProperty("src_file_ids")
        private List<String> srcFileIds;

        @NotBlank(message = "Target folder ID cannot be empty")
        @Schema(description = "Target folder ID", requiredMode = Schema.RequiredMode.REQUIRED, example = "folder_002")
        @JsonProperty("dest_file_id")
        private String destFileId;
    }

    /**
     * Batch delete request (Corresponding API 8: rm)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Batch delete request")
    public static class RemoveReq implements Serializable {

        @NotEmpty(message = "File ID list cannot be empty")
        @Schema(description = "File/folder ID list", requiredMode = Schema.RequiredMode.REQUIRED, example = "[\"file_001\", \"file_002\"]")
        @JsonProperty("file_ids")
        private List<String> fileIds;
    }

    /**
     * Import knowledge base request (Corresponding API 9: convert)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Import knowledge base request")
    public static class ConvertReq implements Serializable {

        @NotEmpty(message = "File ID list cannot be empty")
        @Schema(description = "File ID list", requiredMode = Schema.RequiredMode.REQUIRED, example = "[\"file_001\", \"file_002\"]")
        @JsonProperty("file_ids")
        private List<String> fileIds;

        @NotEmpty(message = "Knowledge base ID list cannot be empty")
        @Schema(description = "Target knowledge base ID list", requiredMode = Schema.RequiredMode.REQUIRED, example = "[\"kb_001\"]")
        @JsonProperty("kb_ids")
        private List<String> kbIds;
    }

    /**
     * List query request (Corresponding API 3: list_files)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "List query request")
    public static class ListReq implements Serializable {

        @Schema(description = "Parent folder ID (query root if empty)", example = "folder_001")
        @JsonProperty("parent_id")
        private String parentId;

        @Schema(description = "Keyword search", example = "Document")
        private String keywords;

        @Schema(description = "Page number (starts from 1)", example = "1")
        private Integer page;

        @Schema(description = "Items per page", example = "30")
        @JsonProperty("page_size")
        private Integer pageSize;

        @Schema(description = "Sort field: create_time / update_time / name / size", example = "create_time")
        private String orderby;

        @Schema(description = "Descending", example = "true")
        private Boolean desc;
    }

    // ========== Responseclass ==========

    /**
     * File/folder basic info VO
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "File/folder basic info")
    public static class InfoVO implements Serializable {

        @Schema(description = "File/folder ID", example = "file_001")
        private String id;

        @Schema(description = "Parent folder ID", example = "folder_001")
        @JsonProperty("parent_id")
        private String parentId;

        @Schema(description = "Tenant ID", example = "tenant_001")
        @JsonProperty("tenant_id")
        private String tenantId;

        @Schema(description = "Creator ID", example = "user_001")
        @JsonProperty("created_by")
        private String createdBy;

        @Schema(description = "Type: FOLDER / FILE", example = "FOLDER")
        private String type;

        @Schema(description = "Name", example = "My folder")
        private String name;

        @Schema(description = "Path location", example = "/root/folder")
        private String location;

        @Schema(description = "File size (bytes)", example = "1024")
        private Long size;

        @Schema(description = "Source type", example = "local")
        @JsonProperty("source_type")
        private String sourceType;

        @Schema(description = "Creation time (timestamp)", example = "1700000000000")
        @JsonProperty("create_time")
        private Long createTime;

        @Schema(description = "Creation date (formatted)", example = "2024-01-15 10:30:00")
        @JsonProperty("create_date")
        private String createDate;

        @Schema(description = "Update time (timestamp)", example = "1700000001000")
        @JsonProperty("update_time")
        private Long updateTime;

        @Schema(description = "Update date (formatted)", example = "2024-01-15 11:00:00")
        @JsonProperty("update_date")
        private String updateDate;

        @Schema(description = "File extension", example = "pdf")
        private String extension;
    }

    /**
     * ListResponse VO (Corresponding API 3: list_files)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "File list response")
    public static class ListVO implements Serializable {

        @Schema(description = "Total records", example = "100")
        private Long total;

        @Schema(description = "Current parent folder info")
        @JsonProperty("parent_folder")
        private InfoVO parentFolder;

        @Schema(description = "File/folder list")
        private List<InfoVO> files;

        @Schema(description = "Breadcrumb navigation path")
        private List<InfoVO> breadcrumb;
    }

    /**
     * Conversion result item VO (Corresponding API 9: convert)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "File conversion result item")
    public static class ConvertVO implements Serializable {

        @Schema(description = "Conversion record ID", example = "convert_001")
        private String id;

        @Schema(description = "Source file ID", example = "file_001")
        @JsonProperty("file_id")
        private String fileId;

        @Schema(description = "Target document ID", example = "doc_001")
        @JsonProperty("document_id")
        private String documentId;

        @Schema(description = "Creation time (timestamp)", example = "1700000000000")
        @JsonProperty("create_time")
        private Long createTime;

        @Schema(description = "Creation date (formatted)", example = "2024-01-15 10:30:00")
        @JsonProperty("create_date")
        private String createDate;

        @Schema(description = "Update time (timestamp)", example = "1700000001000")
        @JsonProperty("update_time")
        private Long updateTime;

        @Schema(description = "Update date (formatted)", example = "2024-01-15 11:00:00")
        @JsonProperty("update_date")
        private String updateDate;
    }

    /**
     * ConvertStatus VO (Corresponding API 10: get_convert_status)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "File conversion status")
    public static class ConvertStatusVO implements Serializable {

        @Schema(description = "ConvertStatus: pending / processing / completed / failed", example = "completed")
        private String status;

        @Schema(description = "Conversion progress (0.0 - 1.0)", example = "1.0")
        private Float progress;

        @Schema(description = "Status message", example = "Conversion completed")
        private String message;
    }

    /**
     * Breadcrumb VO (Corresponding API 12: all_parent_folder)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Breadcrumb navigation (all parent folders)")
    public static class BreadcrumbVO implements Serializable {

        @Schema(description = "Parent folder list (path from root to current)")
        @JsonProperty("parent_folders")
        private List<InfoVO> parentFolders;
    }

    /**
     * Root directory info VO (Corresponding API 10: get_root_folder)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Root directory info")
    public static class RootFolderVO implements Serializable {

        @Schema(description = "Root folder info")
        @JsonProperty("root_folder")
        private InfoVO rootFolder;
    }

    /**
     * Parent directory info VO (Corresponding API 11: get_parent_folder)
     */
    @Data
    @NoArgsConstructor
    @AllArgsConstructor
    @Builder
    @Schema(description = "Parent directory info")
    public static class ParentFolderVO implements Serializable {

        @Schema(description = "Parent folder info")
        @JsonProperty("parent_folder")
        private InfoVO parentFolder;
    }
}
