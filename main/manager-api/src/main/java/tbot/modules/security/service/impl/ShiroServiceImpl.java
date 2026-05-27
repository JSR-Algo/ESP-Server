package tbot.modules.security.service.impl;

import org.springframework.stereotype.Service;

import lombok.AllArgsConstructor;
import tbot.modules.security.dao.SysUserTokenDao;
import tbot.modules.security.entity.SysUserTokenEntity;
import tbot.modules.security.service.ShiroService;
import tbot.modules.sys.dao.SysUserDao;
import tbot.modules.sys.entity.SysUserEntity;

@AllArgsConstructor
@Service
public class ShiroServiceImpl implements ShiroService {
    private final SysUserDao sysUserDao;
    private final SysUserTokenDao sysUserTokenDao;

    @Override
    public SysUserTokenEntity getByToken(String token) {
        return sysUserTokenDao.getByToken(token);
    }

    @Override
    public SysUserEntity getUser(Long userId) {
        return sysUserDao.selectById(userId);
    }
}