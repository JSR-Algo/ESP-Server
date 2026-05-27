package tbot.modules.agent.vo;

import lombok.Data;
import lombok.EqualsAndHashCode;
import tbot.modules.agent.entity.AgentTemplateEntity;

@Data
@EqualsAndHashCode(callSuper = true)
public class AgentTemplateVO extends AgentTemplateEntity {
    // Role Voice
    private String ttsModelName;

    // Role Model
    private String llmModelName;
}
