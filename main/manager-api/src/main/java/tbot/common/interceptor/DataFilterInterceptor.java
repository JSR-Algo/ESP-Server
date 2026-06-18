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

    private static final Pattern UNSAFE_SQL_FILTER = Pattern.compile(
            "(?is)(;|--|/\\*|\\*/|\\bunion\\b|\\bdrop\\b|\\binsert\\b|\\bupdate\\b|\\bdelete\\b|\\bor\\b)");

    @SuppressWarnings("rawtypes")
    @Override
    public void beforeQuery(Executor executor, MappedStatement ms, Object parameter, RowBounds rowBounds,
            ResultHandler resultHandler, BoundSql boundSql) {
        DataScope scope = getDataScope(parameter);
        // No data filtering
        if (scope == null || StrUtil.isBlank(scope.getSqlFilter())) {
            return;
        }

        // Concat newSQL
        String buildSql = getSelect(boundSql.getSql(), scope);

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

    private String getSelect(String buildSql, DataScope scope) {
        Expression dataScopeExpression = parseSafeDataScopeExpression(scope.getSqlFilter());
        try {
            Select select = (Select) CCJSqlParserUtil.parse(buildSql);
            PlainSelect plainSelect = (PlainSelect) select.getSelectBody();

            Expression expression = plainSelect.getWhere();
            if (expression == null) {
                plainSelect.setWhere(dataScopeExpression);
            } else {
                AndExpression andExpression = new AndExpression(expression, dataScopeExpression);
                plainSelect.setWhere(andExpression);
            }

            return select.toString();
        } catch (JSQLParserException e) {
            return buildSql;
        }
    }

    private Expression parseSafeDataScopeExpression(String sqlFilter) {
        if (UNSAFE_SQL_FILTER.matcher(sqlFilter).find()) {
            throw new IllegalArgumentException("Unsafe sqlFilter detected");
        }
        try {
            return CCJSqlParserUtil.parseCondExpression(sqlFilter);
        } catch (JSQLParserException e) {
            throw new IllegalArgumentException("Unsafe sqlFilter detected", e);
        }
    }
}
