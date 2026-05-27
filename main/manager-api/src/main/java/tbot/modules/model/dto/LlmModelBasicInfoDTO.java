package tbot.modules.model.dto;

import lombok.Data;
import lombok.EqualsAndHashCode;

/**
 * LLMBasic display data of model
 */
@EqualsAndHashCode(callSuper = true)
@Data
public class LlmModelBasicInfoDTO extends ModelBasicInfoDTO{
    private String type;
}