package tbot.common.aspect;

import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import lombok.extern.slf4j.Slf4j;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;

/**
 * RedisAspect handler class
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Slf4j
@Aspect
@Component
public class RedisAspect {
    /**
     * EnabledredisCache trueEnable falseClose
     */
    @Value("${renren.redis.open}")
    private boolean open;

    @Around("execution(* tbot.common.redis.RedisUtils.*(..))")
    public Object around(ProceedingJoinPoint point) throws Throwable {
        Object result = null;
        if (open) {
            try {
                result = point.proceed();
            } catch (Exception e) {
                log.error("redis error", e);
                throw new RenException(ErrorCode.REDIS_ERROR);
            }
        }
        return result;
    }
}
