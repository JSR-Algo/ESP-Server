package tbot.modules.security.service.impl;

import java.io.IOException;
import java.security.SecureRandom;
import java.util.concurrent.TimeUnit;

import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import com.google.common.cache.Cache;
import com.google.common.cache.CacheBuilder;
import com.wf.captcha.SpecCaptcha;
import com.wf.captcha.base.Captcha;

import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletResponse;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.modules.security.service.CaptchaService;
import tbot.modules.sms.service.SmsService;
import tbot.modules.sys.service.SysParamsService;

/**
 * Verification code
 */
@Service
public class CaptchaServiceImpl implements CaptchaService {
    @Resource
    private RedisUtils redisUtils;
    @Resource
    private SmsService smsService;
    @Resource
    private SysParamsService sysParamsService;
    @Value("${renren.redis.open}")
    private boolean open;
    /**
     * Local Cache 5Minute Expiry
     */
    Cache<String, String> localCache = CacheBuilder.newBuilder().maximumSize(1000)
            .expireAfterAccess(5, TimeUnit.MINUTES).build();

    @Override
    public void create(HttpServletResponse response, String uuid) throws IOException {
        response.setContentType("image/gif");
        response.setHeader("Pragma", "No-cache");
        response.setHeader("Cache-Control", "no-cache");
        response.setDateHeader("Expires", 0);

        // Generate captcha
        SpecCaptcha captcha = new SpecCaptcha(150, 40);
        captcha.setLen(5);
        captcha.setCharType(Captcha.TYPE_DEFAULT);
        captcha.out(response.getOutputStream());

        // Save to cache
        setCache(uuid, captcha.text());
    }

    @Override
    public boolean validate(String uuid, String code, Boolean delete) {
        if (StringUtils.isBlank(code)) {
            return false;
        }
        // Get verification code
        String captcha = getCache(uuid, delete);

        // Validation Successful
        if (code.equalsIgnoreCase(captcha)) {
            return true;
        }

        return false;
    }

    @Override
    public void sendSMSValidateCode(String phone) {
        // Check send interval
        String lastSendTimeKey = RedisKeys.getSMSLastSendTimeKey(phone);
        // Get whether sent. If not, set last sent time (60seconds)
        String lastSendTime = redisUtils
                .getKeyOrCreate(lastSendTimeKey,
                        String.valueOf(System.currentTimeMillis()), 60L);
        if (lastSendTime != null) {
            long lastSendTimeLong = Long.parseLong(lastSendTime);
            long currentTime = System.currentTimeMillis();
            long timeDiff = currentTime - lastSendTimeLong;
            if (timeDiff < 60000) {
                throw new RenException(ErrorCode.SMS_SEND_TOO_FREQUENTLY, String.valueOf((60000 - timeDiff) / 1000));
            }
        }

        // Check today's send count
        String todayCountKey = RedisKeys.getSMSTodayCountKey(phone);
        Integer todayCount = (Integer) redisUtils.get(todayCountKey);
        if (todayCount == null) {
            todayCount = 0;
        }

        // Get max send count limit
        Integer maxSendCount = sysParamsService.getValueObject(
                Constant.SysMSMParam.SERVER_SMS_MAX_SEND_COUNT.getValue(),
                Integer.class);
        if (maxSendCount == null) {
            maxSendCount = 5; // Default value
        }

        if (todayCount >= maxSendCount) {
            throw new RenException(ErrorCode.TODAY_SMS_LIMIT_REACHED);
        }

        String key = RedisKeys.getSMSValidateCodeKey(phone);
        String validateCodes = generateValidateCode(6);

        // Set captcha
        setCache(key, validateCodes);

        // Update today's send count
        if (todayCount == 0) {
            redisUtils.increment(todayCountKey, RedisUtils.DEFAULT_EXPIRE);
        } else {
            redisUtils.increment(todayCountKey);
        }

        // Send verification SMS
        smsService.sendVerificationCodeSms(phone, validateCodes);
    }

    @Override
    public boolean validateSMSValidateCode(String phone, String code, Boolean delete) {
        String key = RedisKeys.getSMSValidateCodeKey(phone);
        return validate(key, code, delete);
    }

    /**
     * Generate specified count random number verification codes
     * 
     * @param length Quantity
     * @return Random code
     */
    private String generateValidateCode(Integer length) {
        String chars = "0123456789"; // Character range can be custom: digits
        SecureRandom random = new SecureRandom();
        StringBuilder code = new StringBuilder();
        for (int i = 0; i < length; i++) {
            code.append(chars.charAt(random.nextInt(chars.length())));
        }
        return code.toString();
    }

    private void setCache(String key, String value) {
        if (open) {
            key = RedisKeys.getCaptchaKey(key);
            // Set5Minute Expiry
            redisUtils.set(key, value, 300);
        } else {
            localCache.put(key, value);
        }
    }

    private String getCache(String key, Boolean delete) {
        if (open) {
            key = RedisKeys.getCaptchaKey(key);
            String captcha = (String) redisUtils.get(key);
            // Delete captcha
            if (captcha != null && delete) {
                redisUtils.delete(key);
            }

            return captcha;
        }

        String captcha = localCache.getIfPresent(key);
        // Delete captcha
        if (captcha != null) {
            localCache.invalidate(key);
        }
        return captcha;
    }
}