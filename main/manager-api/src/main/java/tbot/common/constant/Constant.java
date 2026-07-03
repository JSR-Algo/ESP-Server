package tbot.common.constant;

import lombok.Getter;

/**
 * Constant
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public interface Constant {
    /**
     * Success
     */
    int SUCCESS = 1;
    /**
     * Fail
     */
    int FAIL = 0;
    /**
     * OK
     */
    String OK = "OK";
    /**
     * User ID
     */
    String USER_KEY = "userId";
    /**
     * Menu root node ID
     */
    Long MENU_ROOT = 0L;
    /**
     * Department root node ID
     */
    Long DEPT_ROOT = 0L;
    /**
     * Data dictionary root node ID
     */
    Long DICT_ROOT = 0L;
    /**
     * Ascending
     */
    String ASC = "asc";
    /**
     * Descending
     */
    String DESC = "desc";
    /**
     * Creation timeField name
     */
    String CREATE_DATE = "create_date";

    /**
     * Creation timeField name
     */
    String ID = "id";

    /**
     * Data permission filter
     */
    String SQL_FILTER = "sqlFilter";

    /**
     * CurrentPage number
     */
    String PAGE = "page";
    /**
     * Records per page
     */
    String LIMIT = "limit";
    /**
     * Sort field
     */
    String ORDER_FIELD = "orderField";
    /**
     * SortMode
     */
    String ORDER = "order";

    /**
     * Request headerAuthorization ID
     */
    String AUTHORIZATION = "Authorization";

    /**
     * ServerKey
     */
    String SERVER_SECRET = "server.secret";

    /**
     * SM2Public key
     */
    String SM2_PUBLIC_KEY = "server.public_key";

    /**
     * SM2Private key
     */
    String SM2_PRIVATE_KEY = "server.private_key";

    /**
     * websocketAddress
     */
    String SERVER_WEBSOCKET = "server.websocket";

    /**
     * firmware-facing backend API base URL
     */
    String SERVER_API_URL = "server.api_url";

    /**
     * mqtt gateway Config
     */
    String SERVER_MQTT_GATEWAY = "server.mqtt_gateway";

    /**
     * otaAddress
     */
    String SERVER_OTA = "server.ota";

    /**
     * Whether allow userRegister
     */
    String SERVER_ALLOW_USER_REGISTER = "server.allow_user_register";

    /**
     * Issue Six DigitsVerification codeControl Panel address shown when
     */
    String SERVER_FRONTED_URL = "server.fronted_url";

    /**
     * Path separator
     */
    String FILE_EXTENSION_SEG = ".";

    /**
     * mcpAccess point path
     */
    String SERVER_MCP_ENDPOINT = "server.mcp_endpoint";

    /**
     * mcpAccess point path
     */
    String SERVER_VOICE_PRINT = "server.voice_print";

    /**
     * mqttKey
     */
    String SERVER_MQTT_SECRET = "server.mqtt_signature_key";

    /**
     * WebSocketAuth Switch
     */
    String SERVER_AUTH_ENABLED = "server.auth.enabled";

    /**
     * No memory
     */
    String MEMORY_NO_MEM = "Memory_nomem";

    /**
     * Only report chat records (notSummary memory)
     */
    String MEMORY_MEM_REPORT_ONLY = "Memory_mem_report_only";

    /**
     * Mem0AIMemory
     */
    String MEMORY_MEM0AI = "Memory_mem0ai";

    /**
     * PowerMemMemory
     */
    String MEMORY_POWERMEM = "Memory_powermem";

    /**
     * Volcengine dual-channel voice cloning
     */
    String VOICE_CLONE_HUOSHAN_DOUBLE_STREAM = "huoshan_double_stream";

    /**
     * RAGConfig Type
     */
    String RAG_CONFIG_TYPE = "RAG";

    enum SysBaseParam {
        /**
         * ICPICP number
         */
        BEIAN_ICP_NUM("server.beian_icp_num"),
        /**
         * GAICP number
         */
        BEIAN_GA_NUM("server.beian_ga_num"),
        /**
         * SystemName
         */
        SERVER_NAME("server.name");

        private String value;

        SysBaseParam(String value) {
            this.value = value;
        }

        public String getValue() {
            return value;
        }
    }

    /**
     * TrainStatus
     */
    enum TrainStatus {
        /**
         * Untrained
         */
        NOT_TRAINED(0),
        /**
         * Training
         */
        TRAINING(1),
        /**
         * Trained
         */
        TRAINED(2),
        /**
         * Training failed
         */
        TRAIN_FAILED(3);

        private final int code;

        TrainStatus(int code) {
            this.code = code;
        }

        public int getCode() {
            return code;
        }
    }

    /**
     * System SMS
     */
    enum SysMSMParam {
        /**
         * Alibaba Cloud authorizationkeyID
         */
        ALIYUN_SMS_ACCESS_KEY_ID("aliyun.sms.access_key_id"),
        /**
         * Alibaba Cloud authorizationKey
         */
        ALIYUN_SMS_ACCESS_KEY_SECRET("aliyun.sms.access_key_secret"),
        /**
         * Alibaba Cloud SMS signature
         */
        ALIYUN_SMS_SIGN_NAME("aliyun.sms.sign_name"),
        /**
         * Alibaba Cloud SMS template
         */
        ALIYUN_SMS_SMS_CODE_TEMPLATE_CODE("aliyun.sms.sms_code_template_code"),
        /**
         * Max SMS sends per single number
         */
        SERVER_SMS_MAX_SEND_COUNT("server.sms_max_send_count"),
        /**
         * Whether enable mobileRegister
         */
        SERVER_ENABLE_MOBILE_REGISTER("server.enable_mobile_register");

        private String value;

        SysMSMParam(String value) {
            this.value = value;
        }

        public String getValue() {
            return value;
        }
    }

    /**
     * DataStatus
     */
    enum DataOperation {
        /**
         * Insert
         */
        INSERT("I"),
        /**
         * alreadyModify
         */
        UPDATE("U"),
        /**
         * alreadyDelete
         */
        DELETE("D");

        private String value;

        DataOperation(String value) {
            this.value = value;
        }

        public String getValue() {
            return value;
        }
    }

    @Getter
    enum ChatHistoryConfEnum {
        IGNORE(0, "Do not record"),
        RECORD_TEXT(1, "Record text"),
        RECORD_TEXT_AUDIO(2, "Record both text and audio");

        private final int code;
        private final String name;

        ChatHistoryConfEnum(int code, String name) {
            this.code = code;
            this.name = name;
        }
    }

    /**
     * Version number
     */
    public static final String VERSION = "0.9.3";

    /**
     * Invalid FirmwareURL
     */
    String INVALID_FIRMWARE_URL = "https://esp.tjbot.vn/tbot/otaMag/download/NOT_ACTIVATED_FIRMWARE_THIS_IS_A_INVALID_URL";

    /**
     * Dictionary type
     */
    enum DictType {
        /**
         * Phone Area Code
         */
        MOBILE_AREA("MOBILE_AREA");

        private String value;

        DictType(String value) {
            this.value = value;
        }

        public String getValue() {
            return value;
        }
    }
}
