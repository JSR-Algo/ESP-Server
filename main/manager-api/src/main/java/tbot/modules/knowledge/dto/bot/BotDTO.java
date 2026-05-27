package tbot.modules.knowledge.dto.bot;

import lombok.*;
import io.swagger.v3.oas.annotations.media.Schema;
import java.io.Serializable;
import java.util.List;
import java.util.Map;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.*;

@Schema(description = "External Bot aggregate DTO")
public class BotDTO {

    // ========== 1. SearchBot (Retrieval robot) ==========

    // Correspond /api/v1/searchbots/ask
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "SearchBot question request")
    public static class SearchAskReq implements Serializable {
        @Schema(description = "User question", requiredMode = Schema.RequiredMode.REQUIRED, example = "What is RAG?")
        @NotBlank(message = "Question cannot be empty")
        @JsonProperty("question")
        private String question;

        @Schema(description = "Return citations", defaultValue = "false")
        @JsonProperty("quote")
        @Builder.Default
        private Boolean quote = false;

        @Schema(description = "Stream return", defaultValue = "true")
        @JsonProperty("stream")
        @Builder.Default
        private Boolean stream = true;
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "SearchBot question response")
    public static class SearchAskVO implements Serializable {
        @Schema(description = "Answer content")
        @JsonProperty("answer")
        private String answer;

        @Schema(description = "Reference source (Value structure usually corresponds to RetrievalDTO.HitVO)")
        @JsonProperty("reference")
        private Map<String, Object> reference;
    }

    // Correspond /api/v1/searchbots/related_questions
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Related question request")
    public static class RelatedQuestionReq implements Serializable {
        @Schema(description = "User question", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotBlank(message = "Question cannot be empty")
        @JsonProperty("question")
        private String question;
    }

    // Correspond /api/v1/searchbots/mindmap
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "Mind map request")
    public static class MindMapReq implements Serializable {
        @Schema(description = "User question", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotBlank(message = "Question cannot be empty")
        @JsonProperty("question")
        private String question;
    }

    // ========== 2. AgentBot (Embedded Agent) ==========

    // Correspond /api/v1/agentbots/{id}/inputs
    @Data
    @Builder
    @AllArgsConstructor
    @Schema(description = "AgentBot input parameter request")
    public static class AgentInputsReq implements Serializable {
    }

    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "AgentBot input parameter definition response")
    public static class AgentInputsVO implements Serializable {
        @Schema(description = "Form variable definition list")
        @JsonProperty("variables")
        private List<Map<String, Object>> variables;
    }

    // Correspond /api/v1/agentbots/{id}/completions
    @Data
    @Builder
    @NoArgsConstructor
    @AllArgsConstructor
    @Schema(description = "AgentBot chat request")
    public static class AgentCompletionReq implements Serializable {
        @Schema(description = "Input parameter value")
        @JsonProperty("inputs")
        private Map<String, Object> inputs;

        @Schema(description = "User query", requiredMode = Schema.RequiredMode.REQUIRED)
        @NotBlank(message = "Query content cannot be empty")
        @JsonProperty("question")
        private String question;

        @Schema(description = "Stream return", defaultValue = "true")
        @JsonProperty("stream")
        @Builder.Default
        private Boolean stream = true;

        @Schema(description = "Session ID")
        @JsonProperty("session_id")
        private String sessionId;
    }
}
