package tbot.modules.sys.entity;

import java.util.Date;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableName;

import lombok.Data;
import lombok.EqualsAndHashCode;
import tbot.common.entity.BaseEntity;

/**
 * System User
 */
@Data
@EqualsAndHashCode(callSuper = false)
@TableName("sys_user")
public class SysUserEntity extends BaseEntity {
    /**
     * Username
     */
    private String username;
    /**
     * Password
     */
    private String password;
    /**
     * Super admin 0: no 1: yes
     */
    private Integer superAdmin;
    /**
     * Status 0: disabled 1: normal
     */
    private Integer status;
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