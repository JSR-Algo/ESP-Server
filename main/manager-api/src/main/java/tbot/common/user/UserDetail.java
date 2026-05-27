package tbot.common.user;

import java.io.Serializable;

import lombok.Data;

/**
 * Login user info
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Data
public class UserDetail implements Serializable {
    private Long id;
    private String username;
    private Integer superAdmin;
    private String token;
    private Integer status;
}