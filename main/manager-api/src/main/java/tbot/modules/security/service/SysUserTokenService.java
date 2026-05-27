package tbot.modules.security.service;

import tbot.common.page.TokenDTO;
import tbot.common.service.BaseService;
import tbot.common.utils.Result;
import tbot.modules.security.entity.SysUserTokenEntity;
import tbot.modules.sys.dto.PasswordDTO;
import tbot.modules.sys.dto.SysUserDTO;

/**
 * UserToken
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public interface SysUserTokenService extends BaseService<SysUserTokenEntity> {

    /**
     * Generatetoken
     *
     * @param userId UserID
     */
    Result<TokenDTO> createToken(Long userId);

    SysUserDTO getUserByToken(String token);

    /**
     * Exit
     *
     * @param userId UserID
     */
    void logout(Long userId);

    /**
     * Change password
     *
     * @param userId
     * @param passwordDTO
     */
    void changePassword(Long userId, PasswordDTO passwordDTO);

}