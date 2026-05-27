package tbot.modules.security.oauth2;

import java.util.HashSet;
import java.util.Set;

import org.apache.shiro.authc.AuthenticationException;
import org.apache.shiro.authc.AuthenticationInfo;
import org.apache.shiro.authc.AuthenticationToken;
import org.apache.shiro.authc.DisabledAccountException;
import org.apache.shiro.authc.IncorrectCredentialsException;
import org.apache.shiro.authc.LockedAccountException;
import org.apache.shiro.authc.SimpleAuthenticationInfo;
import org.apache.shiro.authz.AuthorizationInfo;
import org.apache.shiro.authz.SimpleAuthorizationInfo;
import org.apache.shiro.realm.AuthorizingRealm;
import org.apache.shiro.subject.PrincipalCollection;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Component;

import jakarta.annotation.Resource;
import tbot.common.exception.ErrorCode;
import tbot.common.user.UserDetail;
import tbot.common.utils.ConvertUtils;
import tbot.common.utils.MessageUtils;
import tbot.modules.security.entity.SysUserTokenEntity;
import tbot.modules.security.service.ShiroService;
import tbot.modules.sys.entity.SysUserEntity;
import tbot.modules.sys.enums.SuperAdminEnum;

/**
 * Auth
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Component
public class Oauth2Realm extends AuthorizingRealm {
    @Lazy
    @Resource
    private ShiroService shiroService;

    private static final Logger logger = LoggerFactory.getLogger(Oauth2Realm.class);

    @Override
    public boolean supports(AuthenticationToken token) {
        return token instanceof Oauth2Token;
    }

    /**
     * Authorize(Called during permission validation)
     */
    @Override
    protected AuthorizationInfo doGetAuthorizationInfo(PrincipalCollection principals) {
        UserDetail user = (UserDetail) principals.getPrimaryPrincipal();

        // User permission list
        Set<String> permsSet = new HashSet<>();

        if (user.getSuperAdmin() == SuperAdminEnum.YES.value()) {
            permsSet.add("sys:role:superAdmin");
            permsSet.add("sys:role:normal");
        } else {
            permsSet.add("sys:role:normal");
        }

        SimpleAuthorizationInfo info = new SimpleAuthorizationInfo();
        info.setStringPermissions(permsSet);
        return info;
    }

    /**
     * Auth(LoginCall when)
     */
    @Override
    protected AuthenticationInfo doGetAuthenticationInfo(AuthenticationToken token) throws AuthenticationException {
        String accessToken = (String) token.getPrincipal();

        // Based onaccessTokenQuery userInfo
        SysUserTokenEntity tokenEntity = shiroService.getByToken(accessToken);
        // tokenExpire
        if (tokenEntity == null || tokenEntity.getExpireDate().getTime() < System.currentTimeMillis()) {
            throw new IncorrectCredentialsException(MessageUtils.getMessage(ErrorCode.TOKEN_INVALID));
        }

        // Query UserInfo
        SysUserEntity userEntity = shiroService.getUser(tokenEntity.getUserId());

        // Convert toUserDetailObject
        UserDetail userDetail = ConvertUtils.sourceToTarget(userEntity, UserDetail.class);

        userDetail.setToken(accessToken);

        // Account Locked
        if (userDetail.getStatus() == null) {
            logger.error("Account status abnormal, status cannot be empty");
            throw new DisabledAccountException(MessageUtils.getMessage(ErrorCode.ACCOUNT_DISABLE));
        }

        if (userDetail.getStatus() == 0) {
            throw new LockedAccountException(MessageUtils.getMessage(ErrorCode.ACCOUNT_LOCK));
        }

        SimpleAuthenticationInfo info = new SimpleAuthenticationInfo(userDetail, accessToken, getName());
        return info;
    }

}