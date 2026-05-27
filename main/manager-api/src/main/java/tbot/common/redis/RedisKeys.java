package tbot.common.redis;

/**
 * Redis Key Constants class
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public class RedisKeys {
    /**
     * System ParameterKey
     */
    public static String getSysParamsKey() {
        return "sys:params";
    }

    /**
     * Verification codeKey
     */
    public static String getCaptchaKey(String uuid) {
        return "sys:captcha:" + uuid;
    }

    /**
     * Unregistered device verification codeKey
     */
    public static String getDeviceCaptchaKey(String captcha) {
        return "sys:device:captcha:" + captcha;
    }

    /**
     * UseridofKey
     */
    public static String getUserIdKey(Long userid) {
        return "sys:username:id:" + userid;
    }

    /**
     * Of model nameKey
     */
    public static String getModelNameById(String id) {
        return "model:name:" + id;
    }

    /**
     * Of model configKey
     */
    public static String getModelConfigById(String id) {
        return "model:data:" + id;
    }

    /**
     * Get voice name cachekey
     */
    public static String getTimbreNameById(String id) {
        return "timbre:name:" + id;
    }

    /**
     * Get device count cachekey
     */
    public static String getAgentDeviceCountById(String id) {
        return "agent:device:count:" + id;
    }

    /**
     * Get agent last connection time cachekey
     */
    public static String getAgentDeviceLastConnectedAtById(String id) {
        return "agent:device:lastConnected:" + id;
    }

    /**
     * Get system config cachekey
     */
    public static String getServerConfigKey() {
        return "server:config";
    }

    /**
     * Get voice detail cachekey
     */
    public static String getTimbreDetailsKey(String id) {
        return "timbre:details:" + id;
    }

    /**
     * Get version numberKey
     */
    public static String getVersionKey() {
        return "sys:version";
    }

    /**
     * OTAFirmwareIDofKey
     */
    public static String getOtaIdKey(String uuid) {
        return "ota:id:" + uuid;
    }

    /**
     * OTAFirmware download countKey
     */
    public static String getOtaDownloadCountKey(String uuid) {
        return "ota:download:count:" + uuid;
    }

    /**
     * Get dictionary data cachekey
     */
    public static String getDictDataByTypeKey(String dictType) {
        return "sys:dict:data:" + dictType;
    }

    /**
     * Get agent audioIDCache ofkey
     */
    public static String getAgentAudioIdKey(String uuid) {
        return "agent:audio:id:" + uuid;
    }

    /**
     * Get SMS verification code cachekey
     */
    public static String getSMSValidateCodeKey(String phone) {
        return "sms:Validate:Code:" + phone;
    }

    /**
     * Get SMS verification code last send time cachekey
     */
    public static String getSMSLastSendTimeKey(String phone) {
        return "sms:Validate:Code:" + phone + ":last_send_time";
    }

    /**
     * Get SMS verification code today's send count cachekey
     */
    public static String getSMSTodayCountKey(String phone) {
        return "sms:Validate:Code:" + phone + ":today_count";
    }

    /**
     * Chat historyUUIDMappedKey
     */
    public static String getChatHistoryKey(String uuid) {
        return "agent:chat:history:" + uuid;
    }

    /**
     * Get voice clone audioIDCache ofkey
     */
    public static String getVoiceCloneAudioIdKey(String uuid) {
        return "voiceClone:audio:id:" + uuid;
    }

    /**
     * Get knowledge base cachekey
     */
    public static String getKnowledgeBaseCacheKey(String datasetId) {
        return "knowledge:base:" + datasetId;
    }

    /**
     * Get temporary registered device markkey
     */
    public static String getTmpRegisterMacKey(String deviceId) {
        return "tmp_register_mac:" + deviceId;
    }

    /**
     * OTABind Device
     */
    public static String getOtaActivationCode(String activationCode) {
        return "ota:activation:code:" + activationCode;
    }

    /**
     * OTAGet devicemacRelated Info
     */
    public static String getOtaDeviceActivationInfo(String deviceId) {
        return "ota:activation:data:" + deviceId;
    }

    /**
     * OTAUpload Count
     */
    public static String getOtaUploadCountKey(Long username) {
        return "ota:upload:count:" + username;
    }

}
