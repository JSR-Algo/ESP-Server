package tbot.modules.security.controller;

import java.io.IOException;
import java.util.Calendar;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.apache.commons.lang3.StringUtils;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.servlet.http.HttpServletResponse;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.TokenDTO;
import tbot.common.user.UserDetail;
import tbot.common.utils.JsonUtils;
import tbot.common.utils.Result;
import tbot.common.utils.Sm2DecryptUtil;
import tbot.common.validator.AssertUtils;
import tbot.common.validator.ValidatorUtils;
import tbot.modules.security.dto.LoginDTO;
import tbot.modules.security.dto.SmsVerificationDTO;
import tbot.modules.security.password.PasswordUtils;
import tbot.modules.security.service.CaptchaService;
import tbot.modules.security.service.SysUserTokenService;
import tbot.modules.security.user.SecurityUser;
import tbot.modules.sys.dto.PasswordDTO;
import tbot.modules.sys.dto.RetrievePasswordDTO;
import tbot.modules.sys.dto.SysUserDTO;
import tbot.modules.sys.service.SysDictDataService;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.sys.service.SysUserService;
import tbot.modules.sys.vo.SysDictDataItem;

/**
 * LoginController layer
 */
@Slf4j
@AllArgsConstructor
@RestController
@RequestMapping("/user")
@Tag(name = "Login management")
public class LoginController {
    private final SysUserService sysUserService;
    private final SysUserTokenService sysUserTokenService;
    private final CaptchaService captchaService;
    private final SysParamsService sysParamsService;
    private final SysDictDataService sysDictDataService;

    @GetMapping("/captcha")
    @Operation(summary = "Verification code")
    public void captcha(HttpServletResponse response, String uuid) throws IOException {
        // uuid cannot be empty
        AssertUtils.isBlank(uuid, ErrorCode.IDENTIFIER_NOT_NULL);
        // GenerateVerification code
        captchaService.create(response, uuid);
    }

    @PostMapping("/smsVerification")
    @Operation(summary = "SMS verification code")
    public Result<Void> smsVerification(@RequestBody SmsVerificationDTO dto) {
        // VerifyGraphic CAPTCHA
        boolean validate = captchaService.validate(dto.getCaptchaId(), dto.getCaptcha(), false);
        if (!validate) {
            throw new RenException(ErrorCode.SMS_CAPTCHA_ERROR);
        }

        Boolean isMobileRegister = sysParamsService
                .getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class);
        if (!isMobileRegister) {
            throw new RenException(ErrorCode.MOBILE_REGISTER_DISABLED);
        }
        // SendSMS verification code
        captchaService.sendSMSValidateCode(dto.getPhone());
        return new Result<>();
    }

    @PostMapping("/login")
    @Operation(summary = "Login")
    public Result<TokenDTO> login(@RequestBody LoginDTO login) {
        String password = login.getPassword();

        // Use utility class to decrypt and verifyVerification code
        String actualPassword = Sm2DecryptUtil.decryptAndValidateCaptcha(
                password, login.getCaptchaId(), captchaService, sysParamsService);

        login.setPassword(actualPassword);

        // According toUsernameGet User
        SysUserDTO userDTO = sysUserService.getByUsername(login.getUsername());
        // Check whether user exists
        if (userDTO == null) {
            throw new RenException(ErrorCode.ACCOUNT_PASSWORD_ERROR);
        }
        // DeterminePasswordwhether correct, if different then enterif
        if (!PasswordUtils.matches(login.getPassword(), userDTO.getPassword())) {
            throw new RenException(ErrorCode.ACCOUNT_PASSWORD_ERROR);
        }
        return sysUserTokenService.createToken(userDTO.getId());
    }

    @PostMapping("/register")
    @Operation(summary = "Register")
    public Result<Void> register(@RequestBody LoginDTO login) {
        if (!sysUserService.getAllowUserRegister()) {
            throw new RenException(ErrorCode.USER_REGISTER_DISABLED);
        }

        String password = login.getPassword();

        // Use utility class to decrypt and verifyVerification code
        String actualPassword = Sm2DecryptUtil.decryptAndValidateCaptcha(
                password, login.getCaptchaId(), captchaService, sysParamsService);

        login.setPassword(actualPassword);

        // Whether enable mobileRegister
        Boolean isMobileRegister = sysParamsService
                .getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class);
        boolean validate;
        if (isMobileRegister) {
            // Verify user isPhone number
            boolean validPhone = ValidatorUtils.isValidPhone(login.getUsername());
            if (!validPhone) {
                throw new RenException(ErrorCode.USERNAME_NOT_PHONE);
            }
            // VerifySMS verification codeNormal
            validate = captchaService.validateSMSValidateCode(login.getUsername(), login.getMobileCaptcha(), false);
            if (!validate) {
                throw new RenException(ErrorCode.SMS_CODE_ERROR);
            }
        }

        // According toUsernameGet User
        SysUserDTO userDTO = sysUserService.getByUsername(login.getUsername());
        if (userDTO != null) {
            throw new RenException(ErrorCode.PHONE_ALREADY_REGISTERED);
        }
        userDTO = new SysUserDTO();
        userDTO.setUsername(login.getUsername());
        userDTO.setPassword(login.getPassword());
        sysUserService.save(userDTO);
        return new Result<>();
    }

    @GetMapping("/info")
    @Operation(summary = "Get user info")
    public Result<UserDetail> info() {
        UserDetail user = SecurityUser.getUser();
        Result<UserDetail> result = new Result<>();
        result.setData(user);
        return result;
    }

    @GetMapping("/proxy-auth")
    @Operation(summary = "Validate a manager super-admin token for the nginx NestJS proxy")
    public ResponseEntity<Void> proxyAuth(
            @RequestHeader(value = "Authorization", required = false) String authorization) {
        if (StringUtils.isBlank(authorization) || !authorization.startsWith("Bearer ")) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }

        String token = authorization.substring("Bearer ".length()).trim();
        if (token.isEmpty()) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }

        try {
            SysUserDTO user = sysUserTokenService.getUserByToken(token);
            if (user == null || !Integer.valueOf(1).equals(user.getStatus())) {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
            }
            if (!Integer.valueOf(1).equals(user.getSuperAdmin())) {
                return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
            }
            return ResponseEntity.noContent().build();
        } catch (RenException error) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }
    }

    @PutMapping("/change-password")
    @Operation(summary = "Change user password")
    public Result<?> changePassword(@RequestBody PasswordDTO passwordDTO) {
        // Check Non-empty
        ValidatorUtils.validateEntity(passwordDTO);
        Long userId = SecurityUser.getUserId();
        sysUserTokenService.changePassword(userId, passwordDTO);
        return new Result<>();
    }

    @PutMapping("/retrieve-password")
    @Operation(summary = "Retrieve password")
    public Result<?> retrievePassword(@RequestBody RetrievePasswordDTO dto) {
        // Whether enable mobileRegister
        Boolean isMobileRegister = sysParamsService
                .getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class);
        if (!isMobileRegister) {
            throw new RenException(ErrorCode.RETRIEVE_PASSWORD_DISABLED);
        }
        // Check Non-empty
        ValidatorUtils.validateEntity(dto);
        // Verify user isPhone number
        boolean validPhone = ValidatorUtils.isValidPhone(dto.getPhone());
        if (!validPhone) {
            throw new RenException(ErrorCode.PHONE_FORMAT_ERROR);
        }

        // According toUsernameGet User
        SysUserDTO userDTO = sysUserService.getByUsername(dto.getPhone());
        if (userDTO == null) {
            throw new RenException(ErrorCode.PHONE_NOT_REGISTERED);
        }
        // VerifySMS verification codeNormal
        boolean validate = captchaService.validateSMSValidateCode(dto.getPhone(), dto.getCode(), false);
        // Check whether verified
        if (!validate) {
            throw new RenException(ErrorCode.SMS_CODE_ERROR);
        }

        String password = dto.getPassword();

        // Use utility class to decrypt and verifyVerification code
        String actualPassword = Sm2DecryptUtil.decryptAndValidateCaptcha(
                password, dto.getCaptchaId(), captchaService, sysParamsService);

        dto.setPassword(actualPassword);

        sysUserService.changePasswordDirectly(userDTO.getId(), dto.getPassword());
        return new Result<>();
    }

    @GetMapping("/pub-config")
    @Operation(summary = "Public config")
    public Result<Map<String, Object>> pubConfig() {
        Map<String, Object> config = new HashMap<>();
        config.put("enableMobileRegister", sysParamsService
                .getValueObject(Constant.SysMSMParam.SERVER_ENABLE_MOBILE_REGISTER.getValue(), Boolean.class));
        config.put("version", Constant.VERSION);
        config.put("year", "©" + Calendar.getInstance().get(Calendar.YEAR));
        config.put("allowUserRegister", sysUserService.getAllowUserRegister());
        List<SysDictDataItem> list = sysDictDataService.getDictDataByType(Constant.DictType.MOBILE_AREA.getValue());
        config.put("mobileAreaList", list);
        config.put("beianIcpNum", sysParamsService.getValue(Constant.SysBaseParam.BEIAN_ICP_NUM.getValue(), true));
        config.put("beianGaNum", sysParamsService.getValue(Constant.SysBaseParam.BEIAN_GA_NUM.getValue(), true));
        config.put("name", sysParamsService.getValue(Constant.SysBaseParam.SERVER_NAME.getValue(), true));

        // SM2Public key
        String publicKey = sysParamsService.getValue(Constant.SM2_PUBLIC_KEY, true);
        if (StringUtils.isBlank(publicKey)) {
            throw new RenException(ErrorCode.SM2_KEY_NOT_CONFIGURED);
        }
        config.put("sm2PublicKey", publicKey);

        // Getsystem-web.menuParameter config
        String menuConfig = sysParamsService.getValue("system-web.menu", true);
        if (StringUtils.isNotBlank(menuConfig)) {
            config.put("systemWebMenu", JsonUtils.parseObject(menuConfig, Object.class));
        }

        return new Result<Map<String, Object>>().ok(config);
    }
}
