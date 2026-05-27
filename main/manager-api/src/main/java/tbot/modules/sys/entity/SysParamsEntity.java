package tbot.modules.sys.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;

import lombok.Data;
import lombok.EqualsAndHashCode;
import tbot.common.entity.BaseEntity;

/**
 * Parameter management
 */
@Data
@EqualsAndHashCode(callSuper = false)
@TableName("sys_params")
public class SysParamsEntity extends BaseEntity {
    /**
     * Parameter code
     */
    private String paramCode;
    /**
     * Parameter value
     */
    private String paramValue;
    /**
     * Value Type:string-String,number-Number,boolean-Boolean,array-Array
     */
    private String valueType;
    /**
     * Type 0: system parameter 1: non-system parameter
     */
    private Integer paramType;
    /**
     * Remark
     */
    private String remark;
    /**
     * Updater
     */
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private Long updater;
    /**
     * Update time
     */
    @TableField(fill = FieldFill.INSERT_UPDATE)
    private Date updateDate;

}