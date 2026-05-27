package tbot.modules.sys.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;

import lombok.Data;
import lombok.EqualsAndHashCode;
import tbot.common.entity.BaseEntity;

/**
 * Data Dictionary
 */
@Data
@EqualsAndHashCode(callSuper = false)
@TableName("sys_dict_data")
public class SysDictDataEntity extends BaseEntity {
    /**
     * Dictionary TypeID
     */
    private Long dictTypeId;
    /**
     * Dictionary label
     */
    private String dictLabel;
    /**
     * Dictionary value
     */
    private String dictValue;
    /**
     * Remark
     */
    private String remark;
    /**
     * Sort
     */
    private Integer sort;
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