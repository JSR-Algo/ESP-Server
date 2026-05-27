package tbot.modules.knowledge.dto.agent;

import lombok.*;
import io.swagger.v3.oas.annotations.media.Schema;
import java.io.Serializable;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.*;

@Schema(description = "Agent management aggregate DTO")
public class AgentDTO {

    // ========== 1. Agent Manage (CRUD) - Correspond RAGFlow_AgentAPI Details ==========
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Agent create request")
    public static class CreateReq implements Serializable {
        @Schema(description = "Agent title", requiredMode = Schema.RequiredMode.REQUIRED, example = "My Agent")
        @NotBlank(message = "Agent title cannot be empty")
        @JsonProperty("title")
        private String title;

        @Schema(description = "DSL definition (canvas JSON)", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotNull(message = "DSL definition cannot be empty")
        @JsonProperty("dsl")
        private Map<String, Object> dsl;

        @Schema(description = "Description", example = "This is a test Agent")
        @JsonProperty("description")
        private String description;

        @Schema(description = "Avatar URL", example = "http://example.com/avatar.png")
        @JsonProperty("avatar")
        private String avatar;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Agent update request")
    public static class UpdateReq implements Serializable {
        @Schema(description = "Agent title", example = "Updated Agent")
        @JsonProperty("title")
        private String title;

        @Schema(description = "DSL definition (canvas JSON)")
        @JsonProperty("dsl")
        private Map<String, Object> dsl;

        @Schema(description = "Description")
        @JsonProperty("description")
        private String description;

        @Schema(description = "Avatar URL")
        @JsonProperty("avatar")
        private String avatar;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Agent list request")
    public static class ListReq implements Serializable {
        @Schema(description = "Page number", defaultValue = "1")
        @JsonProperty("page")
        @Builder.Default
        private Integer page = 1;

        @Schema(description = "Page size", defaultValue = "10")
        @JsonProperty("page_size")
        @Builder.Default
        private Integer pageSize = 10;

        @Schema(description = "Sort field", defaultValue = "update_time")
        @JsonProperty("orderby")
        @Builder.Default
        private String orderby = "update_time";

        @Schema(description = "Descending", defaultValue = "true")
        @JsonProperty("desc")
        @Builder.Default
        private Boolean desc = true;

        @Schema(description = "Agent ID filter")
        @JsonProperty("id")
        private String id;

        @Schema(description = "Fuzzy title search")
        @JsonProperty("title")
        private String title;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Agent response object")
    public static class AgentVO implements Serializable {
        @Schema(description = "Agent ID")
        @JsonProperty("id")
        private String id;

        @Schema(description = "Title")
        @JsonProperty("title")
        private String title;

        @Schema(description = "Description")
        @JsonProperty("description")
        private String description;

        @Schema(description = "Avatar")
        @JsonProperty("avatar")
        private String avatar;

        @Schema(description = "DSL definition")
        @JsonProperty("dsl")
        private Map<String, Object> dsl;

        @Schema(description = "Creator ID")
        @JsonProperty("user_id")
        private String userId;

        @Schema(description = "Canvas category")
        @JsonProperty("canvas_category")
        private String canvasCategory;

        @Schema(description = "Creation time (timestamp)")
        @JsonProperty("create_time")
        private Long createTime;

        @Schema(description = "Update time (timestamp)")
        @JsonProperty("update_time")
        private Long updateTime;
    }

    // ========== 2. Webhook Debug and trace - Correspond RAGFlow_AgentAPI Details ==========
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Webhook trigger request (dynamic parameters)")
    public static class WebhookTriggerReq implements Serializable {
        @Schema(description = "Input variable", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotNull(message = "Input variable cannot be empty")
        @JsonProperty("inputs")
        private Map<String, Object> inputs;

        @Schema(description = "Query term", example = "Hello")
        @JsonProperty("query")
        private String query;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Webhook trace request")
    public static class WebhookTraceReq implements Serializable {
        @Schema(description = "Timestamp cursor", example = "1700000000.0")
        @JsonProperty("since_ts")
        private Double sinceTs;

        @Schema(description = "Webhook ID")
        @JsonProperty("webhook_id")
        private String webhookId;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Webhook trace response")
    public static class WebhookTraceVO implements Serializable {
        @Schema(description = "Webhook ID")
        @JsonProperty("webhook_id")
        private String webhookId;

        @Schema(description = "Ended")
        @JsonProperty("finished")
        private Boolean finished;

        @Schema(description = "Timestamp cursor for next query")
        @JsonProperty("next_since_ts")
        private Double nextSinceTs;

        @Schema(description = "Event list")
        @JsonProperty("events")
        private List<TraceEvent> events;

        @Data
        @Builder
        @NoArgsConstructor
        @AllArgsConstructor
        @Schema(description = "Trace event item")
        public static class TraceEvent implements Serializable {
            @Schema(description = "Timestamp")
            @JsonProperty("ts")
            private Double ts;

            @Schema(description = "Event type")
            @JsonProperty("event")
            private String event;

            @Schema(description = "Event data")
            @JsonProperty("data")
            private Object data;
        }
    }

    // ========== 3. Agent Session (Session) - Correspond RAGFlow_Agent_DifyAPI Details ==========
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Session create request")
    public static class SessionCreateReq implements Serializable {
        @Schema(description = "User ID")
        @JsonProperty("user_id")
        private String userId;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Session list request")
    public static class SessionListReq implements Serializable {
        @Schema(description = "Page number", defaultValue = "1")
        @JsonProperty("page")
        @Builder.Default
        private Integer page = 1;

        @Schema(description = "Page size", defaultValue = "10")
        @JsonProperty("page_size")
        @Builder.Default
        private Integer pageSize = 10;

        @Schema(description = "Sort field", defaultValue = "create_time")
        @JsonProperty("orderby")
        @Builder.Default
        private String orderby = "create_time";

        @Schema(description = "Descending", defaultValue = "true")
        @JsonProperty("desc")
        @Builder.Default
        private Boolean desc = true;

        @Schema(description = "Session ID")
        @JsonProperty("id")
        private String id;

        @Schema(description = "User ID")
        @JsonProperty("user_id")
        private String userId;

        @Schema(description = "Whether return DSL")
        @JsonProperty("dsl")
        @Builder.Default
        private Boolean dsl = false;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Session batch delete request")
    public static class SessionBatchDeleteReq implements Serializable {
        @Schema(description = "Session ID list", requiredMode = Schema.RequiredMode.REQUIRED)
        @JsonProperty("ids")
        @NotEmpty(message = "ID list cannot be empty")
        private List<String> ids;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Session response object")
    public static class SessionVO implements Serializable {
        @Schema(description = "Session ID")
        @JsonProperty("id")
        private String id;

        @Schema(description = "Agent ID")
        @JsonProperty("agent_id")
        private String agentId;

        @Schema(description = "User ID")
        @JsonProperty("user_id")
        private String userId;

        @Schema(description = "Source")
        @JsonProperty("source")
        private String source;

        @Schema(description = "DSL definition")
        @JsonProperty("dsl")
        private Map<String, Object> dsl;

        @Schema(description = "Message list")
        @JsonProperty("messages")
        private List<Map<String, Object>> messages;
    }

    // ========== 4. Agent Conversation (Completion) - Correspond RAGFlow_Agent_DifyAPI Details ==========
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Completion chat request")
    public static class CompletionReq implements Serializable {
        @Schema(description = "Session ID", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotBlank(message = "Session ID cannot be empty")
        @JsonProperty("session_id")
        private String sessionId;

        @Schema(description = "User question")
        @JsonProperty("question")
        private String question;

        @Schema(description = "Stream return", defaultValue = "true")
        @JsonProperty("stream")
        @Builder.Default
        private Boolean stream = true;

        @Schema(description = "Whether return trace info", defaultValue = "false")
        @JsonProperty("return_trace")
        @Builder.Default
        private Boolean returnTrace = false;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Completion chat response")
    public static class CompletionVO implements Serializable {
        @Schema(description = "Session ID")
        @JsonProperty("id")
        private String id;

        @Schema(description = "Reply content")
        @JsonProperty("content")
        private String content;

        @Schema(description = "Citation source")
        @JsonProperty("reference")
        private Map<String, Object> reference;

        @Schema(description = "Trace info")
        @JsonProperty("trace")
        private List<Object> trace;
    }

    // ========== 5. Dify Compatible Retrieval - Correspond RAGFlow_Agent_DifyAPI Details ==========
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Dify-compatible retrieval request")
    public static class DifyRetrievalReq implements Serializable {
        @Schema(description = "Knowledge base ID")
        @JsonProperty("knowledge_id")
        private String knowledgeId;

        @Schema(description = "Query term")
        @JsonProperty("query")
        private String query;

        @Schema(description = "Retrieval settings")
        @JsonProperty("retrieval_setting")
        private Map<String, Object> retrievalSetting;

        @Schema(description = "Metadata filter conditions")
        @JsonProperty("metadata_condition")
        private Map<String, Object> metadataCondition;

        @Schema(description = "Whether use knowledge graph")
        @JsonProperty("use_kg")
        private Boolean useKg;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Dify-compatible retrieval response")
    public static class DifyRetrievalVO implements Serializable {
        @Schema(description = "Retrieval result list")
        @JsonProperty("records")
        private List<Record> records;

        @Data
        @Builder
        @NoArgsConstructor
        @AllArgsConstructor
        @Schema(description = "Retrieval record")
        public static class Record implements Serializable {
            @Schema(description = "Content")
            @JsonProperty("content")
            private String content;

            @Schema(description = "Similarity score")
            @JsonProperty("score")
            private Double score;

            @Schema(description = "Title")
            @JsonProperty("title")
            private String title;

            @Schema(description = "Metadata")
            @JsonProperty("metadata")
            private Map<String, Object> metadata;
        }
    }
}
