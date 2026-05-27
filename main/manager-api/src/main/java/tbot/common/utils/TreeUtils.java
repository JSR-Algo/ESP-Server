package tbot.common.utils;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import tbot.common.validator.AssertUtils;

/**
 * Tree structure utility class, e.g.: menus, departments, etc.
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public class TreeUtils {

    /**
     * Based onpid,build tree node
     */
    public static <T extends TreeNode<T>> List<T> build(List<T> treeNodes, Long pid) {
        // pidCannot Be Empty
        AssertUtils.isNull(pid, "pid");

        List<T> treeList = new ArrayList<>();
        for (T treeNode : treeNodes) {
            if (pid.equals(treeNode.getPid())) {
                treeList.add(findChildren(treeNodes, treeNode));
            }
        }

        return treeList;
    }

    /**
     * Find child node
     */
    private static <T extends TreeNode<T>> T findChildren(List<T> treeNodes, T rootNode) {
        for (T treeNode : treeNodes) {
            if (rootNode.getId().equals(treeNode.getPid())) {
                rootNode.getChildren().add(findChildren(treeNodes, treeNode));
            }
        }
        return rootNode;
    }

    /**
     * Build tree node
     */
    public static <T extends TreeNode<T>> List<T> build(List<T> treeNodes) {
        List<T> result = new ArrayList<>();

        // listconvertmap
        Map<Long, T> nodeMap = new LinkedHashMap<>(treeNodes.size());
        for (T treeNode : treeNodes) {
            nodeMap.put(treeNode.getId(), treeNode);
        }

        for (T node : nodeMap.values()) {
            T parent = nodeMap.get(node.getPid());
            if (parent != null && !(node.getId().equals(parent.getId()))) {
                parent.getChildren().add(node);
                continue;
            }

            result.add(node);
        }

        return result;
    }

}