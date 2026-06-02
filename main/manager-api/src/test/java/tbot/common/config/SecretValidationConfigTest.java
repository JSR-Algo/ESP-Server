package tbot.common.config;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

/**
 * Security tests for SecretValidationConfig startup validation.
 */
class SecretValidationConfigTest {

    private SecretValidationConfig createConfig(String dbPassword, String redisPassword, String knife4jPassword) {
        SecretValidationConfig config = new SecretValidationConfig();
        ReflectionTestUtils.setField(config, "dbPassword", dbPassword);
        ReflectionTestUtils.setField(config, "redisPassword", redisPassword);
        ReflectionTestUtils.setField(config, "knife4jPassword", knife4jPassword);
        return config;
    }

    @Test
    @DisplayName("Valid secrets pass validation")
    void validSecretsPass() {
        SecretValidationConfig config = createConfig("strong_db_pass", "strong_redis_pass", "strong_knife4j_pass");
        assertDoesNotThrow(() -> config.run());
    }

    @Test
    @DisplayName("Missing DB password fails validation")
    void missingDbPasswordFails() {
        SecretValidationConfig config = createConfig("", "redis_pass", "knife4j_pass");
        IllegalStateException ex = assertThrows(IllegalStateException.class, () -> config.run());
        assertTrue(ex.getMessage().contains("Production secret validation failed"));
    }

    @Test
    @DisplayName("Missing Redis password logs warning but does not fail")
    void missingRedisPasswordWarns() {
        SecretValidationConfig config = createConfig("db_pass", "", "knife4j_pass");
        // Should not throw because Redis password is warning-only
        assertDoesNotThrow(() -> config.run());
    }

    @Test
    @DisplayName("Short Knife4j password fails validation")
    void shortKnife4jPasswordFails() {
        SecretValidationConfig config = createConfig("db_pass", "redis_pass", "123");
        IllegalStateException ex = assertThrows(IllegalStateException.class, () -> config.run());
        assertTrue(ex.getMessage().contains("Production secret validation failed"));
    }

    @Test
    @DisplayName("Blank Knife4j password is acceptable (feature disabled)")
    void blankKnife4jPasswordIsAcceptable() {
        SecretValidationConfig config = createConfig("db_pass", "redis_pass", "");
        assertDoesNotThrow(() -> config.run());
    }
}
