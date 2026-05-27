package tbot.modules.security.service;

import java.io.IOException;

import jakarta.servlet.http.HttpServletResponse;

/**
 * Verification code
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public interface CaptchaService {

    /**
     * Image captcha
     */
    void create(HttpServletResponse response, String uuid) throws IOException;

    /**
     * Captcha validation
     * 
     * @param uuid   uuid
     * @param code   Verification code
     * @param delete Whether delete verification code
     * @return true: success false: failure
     */
    boolean validate(String uuid, String code, Boolean delete);

    /**
     * Send SMS verification code
     * 
     * @param phone Phone
     */
    void sendSMSValidateCode(String phone);

    /**
     * Verify SMS verification code
     * 
     * @param phone  Phone
     * @param code   Verification code
     * @param delete Whether delete verification code
     * @return true: success false: failure
     */
    boolean validateSMSValidateCode(String phone, String code, Boolean delete);
}
