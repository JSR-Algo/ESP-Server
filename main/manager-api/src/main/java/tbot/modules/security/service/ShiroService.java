package tbot.modules.security.service;

import tbot.modules.security.entity.SysUserTokenEntity;
import tbot.modules.sys.entity.SysUserEntity;

/**
 * shiroRelated API
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public interface ShiroService {

    SysUserTokenEntity getByToken(String token);

    /**
     * According to UserIDQuery user
     *
     * @param userId
     */
    SysUserEntity getUser(Long userId);

}
