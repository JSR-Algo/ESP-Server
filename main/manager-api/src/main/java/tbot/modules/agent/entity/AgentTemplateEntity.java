package tbot.modules.agent.entity;

import java.io.Serializable;
import java.math.BigDecimal;
import java.util.Date;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;

import lombok.Data;

/**
 * Agent config template table
 * 
 * @TableName ai_agent_template
 */
@TableName(value = "ai_agent_template")
@Data
public class AgentTemplateEntity implements Serializable {
    /**
     * Agent unique ID
     */
    @TableId(type = IdType.ASSIGN_UUID)
    private String id;

    /**
     * Agent code
     */
    private String agentCode;

    /**
     * Agent name
     */
    private String agentName;

    /**
     * Speech recognition model ID
     */
    private String asrModelId;

    /**
     * Voice activity detection ID
     */
    private String vadModelId;

    /**
     * Large language model ID
     */
    private String llmModelId;

    /**
     * VLLMModel ID
     */
    private String vllmModelId;

    /**
     * Speech synthesis model ID
     */
    private String ttsModelId;

    /**
     * Voice ID
     */
    private String ttsVoiceId;

    /**
     * Voice Language
     */
    private String ttsLanguage;

    /**
     * TTSVolume
     */
    private Integer ttsVolume;

    /**
     * TTSSpeech rate
     */
    private Integer ttsRate;

    /**
     * TTSPitch
     */
    private Integer ttsPitch;

    /**
     * Memory model ID
     */
    private String memModelId;

    /**
     * Intent model ID
     */
    private String intentModelId;

    /**
     * Chat history config (0Do not record 1Record text only 2Record text and voice)
     */
    private Integer chatHistoryConf;

    /**
     * Role setting parameters
     */
    private String systemPrompt;

    /**
     * Summarize Memory
     */
    private String summaryMemory;
    /**
     * Language code
     */
    private String langCode;

    /**
     * Interaction language
     */
    private String language;

    /**
     * Sort Weight
     */
    private Integer sort;

    /**
     * Creator ID
     */
    private Long creator;

    /**
     * Create time
     */
    private Date createdAt;

    /**
     * Updater ID
     */
    private Long updater;

    /**
     * Update time
     */
    private Date updatedAt;

    @TableField(exist = false)
    private static final long serialVersionUID = 1L;
}