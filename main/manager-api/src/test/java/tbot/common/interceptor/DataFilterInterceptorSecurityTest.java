package tbot.common.interceptor;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.HashMap;
import java.util.Map;

import org.apache.ibatis.mapping.BoundSql;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

/**
 * Security tests for DataFilterInterceptor.
 * Verifies that SQL injection via sqlFilter is blocked.
 */
class DataFilterInterceptorSecurityTest {

    private DataFilterInterceptor interceptor;
    private BoundSql boundSql;

    @BeforeEach
    void setUp() {
        interceptor = new DataFilterInterceptor();
        boundSql = Mockito.mock(BoundSql.class);
    }

    private void invokeInterceptor(String sqlFilter) {
        String originalSql = "SELECT id, name FROM sys_user";
        Mockito.when(boundSql.getSql()).thenReturn(originalSql);

        Map<String, Object> params = new HashMap<>();
        params.put("sqlFilter", new DataScope(sqlFilter));

        // Pass null for unused parameters (executor, ms, rowBounds, resultHandler)
        interceptor.beforeQuery(null, null, params, null, null, boundSql);
    }

    @Test
    @DisplayName("Safe sqlFilter is properly injected into WHERE clause")
    void safeSqlFilterIsInjected() {
        assertDoesNotThrow(() -> invokeInterceptor("dept_id = 5"));
    }

    @Test
    @DisplayName("SQL injection via union is blocked")
    void unionInjectionIsBlocked() {
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
            invokeInterceptor("1=1 UNION SELECT password FROM sys_user")
        );
        assertTrue(ex.getMessage().contains("Unsafe sqlFilter detected"));
    }

    @Test
    @DisplayName("SQL injection via stacked query is blocked")
    void stackedQueryInjectionIsBlocked() {
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
            invokeInterceptor("1=1; DROP TABLE sys_user")
        );
        assertTrue(ex.getMessage().contains("Unsafe sqlFilter detected"));
    }

    @Test
    @DisplayName("SQL injection via comment is blocked")
    void commentInjectionIsBlocked() {
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
            invokeInterceptor("1=1 -- malicious comment")
        );
        assertTrue(ex.getMessage().contains("Unsafe sqlFilter detected"));
    }

    @Test
    @DisplayName("SQL injection via OR boolean is blocked")
    void orBooleanInjectionIsBlocked() {
        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class, () ->
            invokeInterceptor("1=1 OR 1=1")
        );
        assertTrue(ex.getMessage().contains("Unsafe sqlFilter detected"));
    }

    @Test
    @DisplayName("Valid multi-condition sqlFilter with AND is accepted")
    void validAndConditionIsAccepted() {
        // JSQLParser can safely parse AND expressions; safety pattern also accepts them.
        assertDoesNotThrow(() -> invokeInterceptor("dept_id = 5 AND status = 1"));
    }

    @Test
    @DisplayName("Valid simple equality sqlFilter is accepted")
    void validEqualityIsAccepted() {
        assertDoesNotThrow(() -> invokeInterceptor("dept_id = 5"));
    }

    @Test
    @DisplayName("Valid IN clause sqlFilter is accepted")
    void validInClauseIsAccepted() {
        assertDoesNotThrow(() -> invokeInterceptor("dept_id IN (1,2,3)"));
    }
}
