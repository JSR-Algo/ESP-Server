package tbot.modules.sms.service;

/**
 * SMS service method definition interface
 *
 * @author zjy
 * @since 2025-05-12
 */
public interface SmsService {

    /**
     * Send verification SMS
     * @param phone Phone Number
     * @param VerificationCode Verification code
     */
    void sendVerificationCodeSms(String phone, String VerificationCode) ;
}
