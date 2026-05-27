package tbot.modules.sms.service.imp;

import com.aliyun.dysmsapi20170525.Client;
import com.aliyun.dysmsapi20170525.models.SendSmsRequest;
import com.aliyun.dysmsapi20170525.models.SendSmsResponse;
import com.aliyun.teaopenapi.models.Config;
import com.aliyun.teautil.models.RuntimeOptions;
import lombok.AllArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.modules.sms.service.SmsService;
import tbot.modules.sys.service.SysParamsService;

@Service
@AllArgsConstructor
@Slf4j
public class ALiYunSmsService implements SmsService {
    private final SysParamsService  sysParamsService;
    private final RedisUtils redisUtils;

    @Override
    public void sendVerificationCodeSms(String phone, String VerificationCode) {
        Client client = createClient();
        String SignName = sysParamsService.getValue(Constant.SysMSMParam
                .ALIYUN_SMS_SIGN_NAME.getValue(),true);
        String TemplateCode = sysParamsService.getValue(Constant.SysMSMParam
                .ALIYUN_SMS_SMS_CODE_TEMPLATE_CODE.getValue(),true);
        try {
            SendSmsRequest sendSmsRequest = new SendSmsRequest()
                    .setSignName(SignName)
                    .setTemplateCode(TemplateCode)
                    .setPhoneNumbers(phone)
                    .setTemplateParam(String.format("{\"code\":\"%s\"}", VerificationCode));
            RuntimeOptions runtime = new RuntimeOptions();
            // Print yourself when copying code to run API return value
            SendSmsResponse sendSmsResponse = client.sendSmsWithOptions(sendSmsRequest, runtime);
            log.info("SMS response requestID: {}", sendSmsResponse.getBody().getRequestId());
        } catch (Exception e) {
            // If send fails, refund this send count
            String todayCountKey = RedisKeys.getSMSTodayCountKey(phone);
            redisUtils.delete(todayCountKey);
            // Error message
            log.error(e.getMessage());
            throw new RenException(ErrorCode.SMS_SEND_FAILED);
        }

    }


    /**
     * Create Alibaba Cloud connection
     * @return Return connection object
     */
    private Client createClient(){
        String ACCESS_KEY_ID = sysParamsService.getValue(Constant.SysMSMParam
                .ALIYUN_SMS_ACCESS_KEY_ID.getValue(),true);
        String ACCESS_KEY_SECRET = sysParamsService.getValue(Constant.SysMSMParam
                .ALIYUN_SMS_ACCESS_KEY_SECRET.getValue(),true);
        try {
            Config config = new Config()
                    .setAccessKeyId(ACCESS_KEY_ID)
                    .setAccessKeySecret(ACCESS_KEY_SECRET);
            // Config Endpoint. China site usedysmsapi.aliyuncs.com
            config.endpoint = "dysmsapi.aliyuncs.com";
            return new Client(config);
        }catch (Exception e){
            // Error message
            log.error(e.getMessage());
            throw new RenException(ErrorCode.SMS_CONNECTION_FAILED);
        }
    }
}
