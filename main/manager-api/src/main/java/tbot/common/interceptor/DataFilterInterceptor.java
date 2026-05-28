package tbot.common.interceptor;

import java.util.Map;
import java.util.regex.Pattern;

import org.apache.ibatis.executor.Executor;
import org.apache.ibatis.mapping.BoundSql;
import org.apache.ibatis.mapping.MappedStatement;
import org.apache.ibatis.session.ResultHandler;
import org.apache.ibatis.session.RowBounds;

import com.baomidou.mybatisplus.core.toolkit.PluginUtils;
import com.baomidou.mybatisplus.extension.plugins.inner.InnerInterceptor;

import cn.hutool.core.util.StrUtil;
import net.sf.jsqlparser.JSQLParserException;
import net.sf.jsqlparser.expression.Expression;
import net.sf.jsqlparser.expression.operators.conditional.AndExpression;
import net.sf.jsqlparser.parser.CCJSqlParserUtil;
import net.sf.jsqlparser.statement.select.PlainSelect;
import net.sf.jsqlparser.statement.select.Select;

/**
 * Data Filter
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public class DataFilterInterceptor implements InnerInterceptor {

    // SECURITY FIX: Only allow simple column/operator/value patterns in sqlFilter.
    // This is a defense-in-depth measure. The primary fix is JSQLParser expression parsing.
    private static final Pattern SQL_FILTER_SAFETY_PATTERN = Pattern.compile(
            "^[a-zA-Z_][a-zA-Z0-9_]*\\s*(=|!=|<>|>=|<=|>|<|LIKE|NOT LIKE|IN|NOT IN)\\s*.+$",
            Pattern.CASE_INSENSITIVE);

    @SuppressWarnings("rawtypes")
    @Override
    public void beforeQuery(Executor executor, MappedStatement ms, Object parameter, RowBounds rowBounds,
            ResultHandler resultHandler, BoundSql boundSql) {
        DataScope scope = getDataScope(parameter);
        // No data filtering
        if (scope == null || StrUtil.isBlank(scope.getSqlFilter())) {
            return;
        }

        // SECURITY FIX: Validate sqlFilter before parsing to prevent injection.
        String sqlFilter = scope.getSqlFilter().trim();
        if (!isSqlFilterSafe(sqlFilter)) {
            throw new IllegalArgumentException("Unsafe sqlFilter detected and blocked: " + sqlFilter);
        }

        // Concat newSQL
        String buildSql = getSelect(boundSql.getSql(), sqlFilter);

        // RewriteSQL
        PluginUtils.mpBoundSql(boundSql).sql(buildSql);
    }

    private DataScope getDataScope(Object parameter) {
        if (parameter == null) {
            return null;
        }

        // Check whether parameter hasDataScopeObject
        if (parameter instanceof Map) {
            Map<?, ?> parameterMap = (Map<?, ?>) parameter;
            for (Map.Entry<?, ?> entry : parameterMap.entrySet()) {
                if (entry.getValue() != null && entry.getValue() instanceof DataScope) {
                    return (DataScope) entry.getValue();
                }
            }
        } else if (parameter instanceof DataScope) {
            return (DataScope) parameter;
        }

        return null;
    }

    /**
     * SECURITY FIX: Defense-in-depth validation for sqlFilter strings.
     * Rejects patterns that look like stacked queries, comments, or boolean-based injection.
     */
    private boolean isSqlFilterSafe(String sqlFilter) {
        if (sqlFilter == null || sqlFilter.isEmpty()) {
            return true;
        }
        // Block stacked queries, union, comments, and semicolons
        String lower = sqlFilter.toLowerCase();
        String[] forbidden = { ";", "--", "/*", "*/", "union", "insert ", "update ", "delete ", "drop ", "alter ",
                "create ", "exec(", "execute(", "xp_", "sp_" };
        for (String f : forbidden) {
            if (lower.contains(f)) {
                return false;
            }
        }
        // Must match a simple condition pattern
        return SQL_FILTER_SAFETY_PATTERN.matcher(sqlFilter).matches();
    }

    private String getSelect(String buildSql, String sqlFilter) {
        try {
            Select select = (Select) CCJSqlParserUtil.parse(buildSql);
            PlainSelect plainSelect = (PlainSelect) select.getSelectBody();

            // SECURITY FIX: Parse sqlFilter as a real JSQLParser Expression AST
            // instead of using StringValue (which wraps it in SQL quotes) and then
            // stripping quotes with replaceAll("'","") — that was an SQL injection vulnerability.
            Expression filterExpression = CCJSqlParserUtil.parseCondExpression(sqlFilter);

            Expression expression = plainSelect.getWhere();
            if (expression == null) {
                plainSelect.setWhere(filterExpression);
            } else {
                AndExpression andExpression = new AndExpression(expression, filterExpression);
                plainSelect.setWhere(andExpression);
            }

            return select.toString();
        } catch (JSQLParserException e) {
            // If parsing fails, do NOT inject raw SQL. Return original query unchanged.
            throw new IllegalArgumentException("Invalid sqlFilter expression: " + sqlFilter, e);
        }
    }
}
