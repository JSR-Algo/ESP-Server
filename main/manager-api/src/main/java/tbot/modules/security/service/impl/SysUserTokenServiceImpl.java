package tbot.modules.security.service.impl;

import java.util.Date;

import org.springframework.stereotype.Service;

import cn.hutool.core.date.DateUtil;
import lombok.AllArgsConstructor;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.TokenDTO;
import tbot.common.service.impl.BaseServiceImpl;
import tbot.common.utils.HttpContextUtils;
import tbot.common.utils.Result;
import tbot.modules.security.dao.SysUserTokenDao;
import tbot.modules.security.entity.SysUserTokenEntity;
import tbot.modules.security.oauth2.TokenGenerator;
import tbot.modules.security.service.SysUserTokenService;
import tbot.modules.sys.dto.PasswordDTO;
import tbot.modules.sys.dto.SysUserDTO;
import tbot.modules.sys.service.SysUserService;

@AllArgsConstructor
@Service
public class SysUserTokenServiceImpl extends BaseServiceImpl<SysUserTokenDao, SysUserTokenEntity>
        implements SysUserTokenService {

    private final SysUserService sysUserService;
    /**
     * 12Expire after hours
     */
    private final static int EXPIRE = 3600 * 12;

    @Override
    public Result<TokenDTO> createToken(Long userId) {
        // Usertoken
        String token;

        // Current Time
        Date now = new Date();
        // Expiration Time
        Date expireTime = new Date(now.getTime() + EXPIRE * 1000);

        // Determine generated beforetoken
        SysUserTokenEntity tokenEntity = baseDao.getByUserId(userId);
        if (tokenEntity == null) {
            // Generate Onetoken
            token = TokenGenerator.generateValue();

            tokenEntity = new SysUserTokenEntity();
            tokenEntity.setUserId(userId);
            tokenEntity.setToken(token);
            tokenEntity.setUpdateDate(now);
            tokenEntity.setExpireDate(expireTime);

            // Savetoken
            this.insert(tokenEntity);
        } else {
            // DeterminetokenExpired
            if (tokenEntity.getExpireDate().getTime() < System.currentTimeMillis()) {
                // tokenExpired, regeneratetoken
                token = TokenGenerator.generateValue();
            } else {
                token = tokenEntity.getToken();
            }

            tokenEntity.setToken(token);
            tokenEntity.setUpdateDate(now);
            tokenEntity.setExpireDate(expireTime);

            // Updatetoken
            this.updateById(tokenEntity);
        }

        String clientHash = HttpContextUtils.getClientCode();

        TokenDTO tokenDTO = new TokenDTO();
        tokenDTO.setToken(token);
        tokenDTO.setExpire(EXPIRE);
        tokenDTO.setClientHash(clientHash);
        return new Result<TokenDTO>().ok(tokenDTO);
    }

    @Override
    public SysUserDTO getUserByToken(String token) {
        SysUserTokenEntity userToken = baseDao.getByToken(token);
        if (null == userToken) {
            throw new RenException(ErrorCode.TOKEN_INVALID);
        }

        Date now = new Date();
        if (userToken.getExpireDate().before(now)) {
            throw new RenException(ErrorCode.UNAUTHORIZED);
        }

        SysUserDTO userDTO = sysUserService.getByUserId(userToken.getUserId());
        userDTO.setPassword("");
        return userDTO;
    }

    @Override
    public void logout(Long userId) {
        Date expireDate = DateUtil.offsetMinute(new Date(), -1);
        baseDao.logout(userId, expireDate);
    }

    @Override
    public void changePassword(Long userId, PasswordDTO passwordDTO) {
        // Change password
        sysUserService.changePassword(userId, passwordDTO);

        // make token Invalid, need log in again after
        Date expireDate = DateUtil.offsetMinute(new Date(), -1);
        baseDao.logout(userId, expireDate);
    }
}