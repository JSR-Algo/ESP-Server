package tbot.modules.sys;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.util.Map;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.utils.SM2Utils;
import tbot.common.utils.Result;
import tbot.modules.security.controller.LoginController;
import tbot.modules.security.dto.LoginDTO;
import tbot.modules.security.dto.SmsVerificationDTO;
import tbot.modules.security.service.CaptchaService;
import tbot.modules.security.service.SysUserTokenService;
import tbot.modules.sys.dto.RetrievePasswordDTO;
import tbot.modules.sys.dto.SysUserDTO;
import tbot.modules.sys.service.SysDictDataService;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.sys.service.SysUserService;

class loginControllerTest {

    private static final String CAPTCHA_ID = "captcha-123";
    private static final String CAPTCHA = "12345";
    private static final String PHONE = "+8613800138000";

    private LoginController loginController;
    private SysUserService sysUserService;
    private CaptchaService captchaService;
    private SysParamsService sysParamsService;
    private SysUserTokenService sysUserTokenService;

    private String privateKey;
    private String encryptedPassword;

    @BeforeEach
    void setUp() {
        sysUserService = mock(SysUserService.class);
        sysUserTokenService = mock(SysUserTokenService.class);
        captchaService = mock(CaptchaService.class);
        sysParamsService = mock(SysParamsService.class);
        SysDictDataService sysDictDataService = mock(SysDictDataService.class);

        loginController = new LoginController(
                sysUserService,
                sysUserTokenService,
                captchaService,
                sysParamsService,
                sysDictDataService);

        Map<String, String> keyPair = SM2Utils.createKey();
        privateKey = keyPair.get(SM2Utils.KEY_PRIVATE_KEY);
        encryptedPassword = SM2Utils.encrypt(keyPair.get(SM2Utils.KEY_PUBLIC_KEY), CAPTCHA + "StrongPass1!");

        when(sysParamsService.getValue(Constant.SM2_PRIVATE_KEY, true)).thenReturn(privateKey);
        when(captchaService.validate(eq(CAPTCHA_ID), eq(CAPTCHA), eq(true))).thenReturn(true);
    }

    @Test
    @DisplayName("proxy auth accepts only an active super admin manager token")
    void proxyAuthAcceptsSuperAdmin() {
        SysUserDTO user = new SysUserDTO();
        user.setStatus(1);
        user.setSuperAdmin(1);
        when(sysUserTokenService.getUserByToken("manager-token")).thenReturn(user);

        ResponseEntity<Void> response = loginController.proxyAuth("Bearer manager-token");

        assertEquals(HttpStatus.NO_CONTENT, response.getStatusCode());
    }

    @Test
    @DisplayName("proxy auth rejects a valid non-super-admin manager token")
    void proxyAuthRejectsNonSuperAdmin() {
        SysUserDTO user = new SysUserDTO();
        user.setStatus(1);
        user.setSuperAdmin(0);
        when(sysUserTokenService.getUserByToken("manager-token")).thenReturn(user);

        ResponseEntity<Void> response = loginController.proxyAuth("Bearer manager-token");

        assertEquals(HttpStatus.FORBIDDEN, response.getStatusCode());
    }

    @Test
    @DisplayName("proxy auth rejects missing, malformed, and invalid manager tokens")
    void proxyAuthRejectsInvalidTokens() {
        when(sysUserTokenService.getUserByToken("invalid"))
                .thenThrow(mock(RenException.class));

        assertEquals(HttpStatus.UNAUTHORIZED, loginController.proxyAuth(null).getStatusCode());
        assertEquals(HttpStatus.UNAUTHORIZED, loginController.proxyAuth("invalid").getStatusCode());
        assertEquals(
                HttpStatus.UNAUTHORIZED,
                loginController.proxyAuth("Bearer invalid").getStatusCode());
    }

    @Test
    @DisplayName("register stores user when registration and SMS verification are valid")
    void testRegister() {
        LoginDTO loginDTO = new LoginDTO();
        loginDTO.setUsername(PHONE);
        loginDTO.setPassword(encryptedPassword);
        loginDTO.setCaptchaId(CAPTCHA_ID);
        loginDTO.setMobileCaptcha("654321");

        when(sysUserService.getAllowUserRegister()).thenReturn(true);
        when(sysParamsService.getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class))
                .thenReturn(true);
        when(captchaService.validateSMSValidateCode(PHONE, "654321", false)).thenReturn(true);
        when(sysUserService.getByUsername(PHONE)).thenReturn(null);

        Result<Void> result = loginController.register(loginDTO);

        assertEquals(0, result.getCode());
        assertEquals("success", result.getMsg());
        verify(sysUserService).save(any(SysUserDTO.class));
    }

    @Test
    @DisplayName("smsVerification sends SMS when captcha and feature flag are valid")
    void testSmsVerification() {
        SmsVerificationDTO smsVerificationDTO = new SmsVerificationDTO();
        smsVerificationDTO.setPhone(PHONE);
        smsVerificationDTO.setCaptchaId("captcha-image");
        smsVerificationDTO.setCaptcha("image1");

        when(captchaService.validate("captcha-image", "image1", false)).thenReturn(true);
        when(sysParamsService.getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class))
                .thenReturn(true);

        Result<Void> result = loginController.smsVerification(smsVerificationDTO);

        assertEquals(0, result.getCode());
        assertEquals("success", result.getMsg());
        verify(captchaService).sendSMSValidateCode(PHONE);
    }

    @Test
    @DisplayName("retrievePassword updates password after SMS and SM2 validation")
    void testRetrievePassword() {
        RetrievePasswordDTO retrievePasswordDTO = new RetrievePasswordDTO();
        retrievePasswordDTO.setCode("654321");
        retrievePasswordDTO.setPhone(PHONE);
        retrievePasswordDTO.setPassword(encryptedPassword);
        retrievePasswordDTO.setCaptchaId(CAPTCHA_ID);

        SysUserDTO userDTO = new SysUserDTO();
        userDTO.setId(7L);
        userDTO.setUsername(PHONE);

        when(sysParamsService.getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class))
                .thenReturn(true);
        when(sysUserService.getByUsername(PHONE)).thenReturn(userDTO);
        when(captchaService.validateSMSValidateCode(PHONE, "654321", false)).thenReturn(true);

        Result<?> result = loginController.retrievePassword(retrievePasswordDTO);

        assertEquals(0, result.getCode());
        assertEquals("success", result.getMsg());
        verify(sysUserService).changePasswordDirectly(7L, "StrongPass1!");
    }

    @Test
    @DisplayName("retrievePassword rejects invalid SMS code before password change")
    void testRetrievePasswordRejectsInvalidSmsCode() {
        RetrievePasswordDTO retrievePasswordDTO = new RetrievePasswordDTO();
        retrievePasswordDTO.setCode("654321");
        retrievePasswordDTO.setPhone(PHONE);
        retrievePasswordDTO.setPassword(encryptedPassword);
        retrievePasswordDTO.setCaptchaId(CAPTCHA_ID);

        SysUserDTO userDTO = new SysUserDTO();
        userDTO.setId(7L);
        userDTO.setUsername(PHONE);

        when(sysParamsService.getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class))
                .thenReturn(true);
        when(sysUserService.getByUsername(PHONE)).thenReturn(userDTO);
        when(captchaService.validateSMSValidateCode(PHONE, "654321", false)).thenReturn(false);

        RenException exception = assertThrows(RenException.class,
                () -> loginController.retrievePassword(retrievePasswordDTO));

        assertEquals(ErrorCode.SMS_CODE_ERROR, exception.getCode());
        verify(sysUserService, never()).changePasswordDirectly(any(), any());
    }
}
