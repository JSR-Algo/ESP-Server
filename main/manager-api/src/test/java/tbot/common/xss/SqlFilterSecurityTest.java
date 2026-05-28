package tbot.common.xss;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Security tests for legacy SqlFilter.
 * Confirms blacklist behavior and documents its deprecation.
 * Note: SqlFilter throws RenException which requires Spring MessageSource.
 * In plain unit tests this may manifest as RuntimeException/NullPointerException
 * because MessageUtils tries to access the Spring context.
 */
class SqlFilterSecurityTest {

    @Test
    @DisplayName("Null or blank input returns null")
    void nullInputReturnsNull() {
        assertNull(SqlFilter.sqlInject(null));
        assertNull(SqlFilter.sqlInject(""));
        assertNull(SqlFilter.sqlInject("   "));
    }

    @Test
    @DisplayName("Keywords in blacklist throw exception")
    void blacklistKeywordsThrow() {
        // Expect some kind of RuntimeException because RenException constructor
        // needs Spring MessageSource which isn't available in plain unit tests.
        assertThrows(RuntimeException.class, () -> SqlFilter.sqlInject("select * from users"));
        assertThrows(RuntimeException.class, () -> SqlFilter.sqlInject("INSERT INTO users"));
        assertThrows(RuntimeException.class, () -> SqlFilter.sqlInject("DROP TABLE users"));
        assertThrows(RuntimeException.class, () -> SqlFilter.sqlInject("update users set"));
    }

    @Test
    @DisplayName("Safe string passes through unchanged")
    void safeStringPasses() {
        String result = SqlFilter.sqlInject("hello world");
        assertEquals("hello world", result);
    }

    @Test
    @DisplayName("Single quotes are stripped (legacy behavior)")
    void singleQuotesStripped() {
        String result = SqlFilter.sqlInject("it's a test");
        assertEquals("its a test", result);
    }

    @Test
    @DisplayName("Case-insensitive keyword detection")
    void caseInsensitiveDetection() {
        assertThrows(RuntimeException.class, () -> SqlFilter.sqlInject("SELECT * FROM users"));
        assertThrows(RuntimeException.class, () -> SqlFilter.sqlInject("SeLeCt * FROM users"));
    }
}
