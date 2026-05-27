package tbot.common.annotation;

import java.lang.annotation.Documented;
import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * Data filter annotation
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
@Documented
public @interface DataFilter {
    /**
     * Table Alias
     */
    String tableAlias() default "";

    /**
     * UserID
     */
    String userId() default "creator";

    /**
     * DepartmentID
     */
    String deptId() default "dept_id";

}