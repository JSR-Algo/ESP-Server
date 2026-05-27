package tbot.common.interceptor;

/**
 * Data Scope
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public class DataScope {
    private String sqlFilter;

    public DataScope(String sqlFilter) {
        this.sqlFilter = sqlFilter;
    }

    public String getSqlFilter() {
        return sqlFilter;
    }

    public void setSqlFilter(String sqlFilter) {
        this.sqlFilter = sqlFilter;
    }

    @Override
    public String toString() {
        return this.sqlFilter;
    }
}