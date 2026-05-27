package tbot.common.page;

import java.io.Serializable;
import java.util.List;

import io.swagger.v3.oas.annotations.media.Schema;
import lombok.Data;

/**
 * PaginationUtility class
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Data
@Schema(description = "Paged data")
public class PageData<T> implements Serializable {
    @Schema(description = "Total records")
    private int total;

    @Schema(description = "List data")
    private List<T> list;

    /**
     * Pagination
     *
     * @param list  List data
     * @param total Total records
     */
    public PageData(List<T> list, long total) {
        this.list = list;
        this.total = (int) total;
    }
}