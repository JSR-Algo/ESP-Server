package xiaozhi.modules.device.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.Size;
import lombok.Data;

import java.io.Serializable;

/**
 * 设备更新DTO
 */
@Data
public class DeviceUpdateDTO implements Serializable {
    /**
    * 自动更新状态
    */
    @Max(1)
    @Min(0)
    private Integer autoUpdate;

    /**
    * 设备别名
    */
    @Size(max = 64)
    private String alias;

    /**
    * 儿童称呼，用于个性化对话
    */
    @Size(max = 64)
    private String childName;

    /**
    * 儿童年龄，用于年龄适配对话
    */
    @Max(18)
    @Min(1)
    private Integer childAge;

    /**
    * 儿童兴趣标签，用于课程个性化
    */
    @Size(max = 255)
    private String childInterests;

    /**
    * 学习风格，用于课程个性化
    */
    @Size(max = 32)
    private String learningStyle;

    /**
    * 词汇水平，用于课程个性化
    */
    @Size(max = 32)
    private String vocabularyLevel;

    /**
    * 家长职业主题，用于课程个性化
    */
    @Size(max = 64)
    private String parentCareer;

    private static final long serialVersionUID = 1L;
}
