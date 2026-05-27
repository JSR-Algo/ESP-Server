package tbot.common.utils;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;

import lombok.Data;

/**
 * Tree node. All needing implement tree node must inherit this class
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
@Data
public class TreeNode<T> implements Serializable {

    /**
     * Primary key
     */
    private Long id;
    /**
     * ParentID
     */
    private Long pid;
    /**
     * Child node list
     */
    private List<T> children = new ArrayList<>();

}