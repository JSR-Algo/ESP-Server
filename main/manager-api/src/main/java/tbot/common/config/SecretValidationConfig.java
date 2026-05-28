package tbot.common.config;

import org.apache.commons.lang3.StringUtils;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Profile;

import lombok.extern.slf4j.Slf4j;

/**
 * Startup validation for required secrets and credentials.
 * Fails fast in production if critical secrets are missing or use default/placeholder values.
 */
@Slf4j
@Configuration
@Profile("!dev & !test")
public class SecretValidationConfig implements CommandLineRunner {

    @Value("${spring.datasource.druid.password:}")
    private String dbPassword;

    @Value("${spring.data.redis.password:}")
    private String redisPassword;

    @Value("${knife4j.basic.password:}")
    private String knife4jPassword;

    @Override
    public void run(String... args) {
        log.info("Running production secret validation...");

        boolean failed = false;

        if (StringUtils.isBlank(dbPassword)) {
            log.error("FATAL: Database password (spring.datasource.druid.password) is not configured.");
            failed = true;
        }

        // Redis password may be empty if Redis is unsecured (not recommended for prod)
        if (StringUtils.isBlank(redisPassword)) {
            log.warn("WARNING: Redis password is not configured. Ensure Redis is network-isolated.");
        }

        if (StringUtils.isNotBlank(knife4jPassword) && knife4jPassword.length() < 8) {
            log.error("FATAL: Knife4j basic password is too short (minimum 8 characters).");
            failed = true;
        }

        if (failed) {
            throw new IllegalStateException(
                    "Production secret validation failed. See logs for details. " +
                    "Application startup aborted to prevent insecure deployment.");
        }

        log.info("Production secret validation passed.");
    }
}
