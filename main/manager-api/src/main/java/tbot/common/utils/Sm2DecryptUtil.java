package tbot.common.utils;

import org.apache.commons.lang3.StringUtils;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.modules.security.service.CaptchaService;
import tbot.modules.sys.service.SysParamsService;

/**
 * SM2Decryption and captcha verification utility class
 * Wrapped repeatedSM2Decrypt, verification code extraction and validation logic
 */
public class Sm2DecryptUtil {

    /**
     * Captcha length
     */
    private static final int CAPTCHA_LENGTH = 5;

    /**
     * DecryptSM2Encrypted content, extract verification code and validate
     * 
     * @param encryptedPassword SM2Encrypted password string
     * @param captchaId         Verification codeID
     * @param captchaService    Captcha service
     * @param sysParamsService  System parameter service
     * @return Actual decrypted password
     */
    public static String decryptAndValidateCaptcha(String encryptedPassword, String captchaId,
            CaptchaService captchaService, SysParamsService sysParamsService) {
        // GetSM2Private key
        String privateKeyStr = sysParamsService.getValue(Constant.SM2_PRIVATE_KEY, true);
        if (StringUtils.isBlank(privateKeyStr)) {
            throw new RenException(ErrorCode.SM2_KEY_NOT_CONFIGURED);
        }

        // UseSM2Private key decrypt password
        String decryptedContent;
        try {
            decryptedContent = SM2Utils.decrypt(privateKeyStr, encryptedPassword);
        } catch (Exception e) {
            throw new RenException(ErrorCode.SM2_DECRYPT_ERROR);
        }

        // Separate verification code and password: first5digits are captcha, rest is password
        if (decryptedContent.length() > CAPTCHA_LENGTH) {
            String embeddedCaptcha = decryptedContent.substring(0, CAPTCHA_LENGTH);
            String actualPassword = decryptedContent.substring(CAPTCHA_LENGTH);

            boolean embeddedCaptchaValid = captchaService.validate(captchaId, embeddedCaptcha, true);
            if (!embeddedCaptchaValid) {
                throw new RenException(ErrorCode.SMS_CAPTCHA_ERROR);
            }

            return actualPassword;
        } else if (decryptedContent.length() > 0) {
            throw new RenException(ErrorCode.SMS_CAPTCHA_ERROR);
        } else {
            throw new RenException(ErrorCode.SM2_DECRYPT_ERROR);
        }
    }
}