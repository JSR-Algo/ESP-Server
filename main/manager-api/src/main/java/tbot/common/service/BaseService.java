package tbot.common.service;

import java.io.Serializable;
import java.util.Collection;

import com.baomidou.mybatisplus.core.conditions.Wrapper;

/**
 * Base service interface, allServiceInterfaces must extend
 * Copyright (c) Renren Open Source All rights reserved.
 * Website: https://www.renren.io
 */
public interface BaseService<T> {
    Class<T> currentModelClass();

    /**
     * <p>
     * Insert one record (select fields, policy insert)
     * </p>
     *
     * @param entity Entity Object
     */
    boolean insert(T entity);

    /**
     * <p>
     * Insert (batch), this method does not support Oracle,SQL Server
     * </p>
     *
     * @param entityList Entity object collection
     */
    boolean insertBatch(Collection<T> entityList);

    /**
     * <p>
     * Insert (batch), this method does not support Oracle,SQL Server
     * </p>
     *
     * @param entityList Entity object collection
     * @param batchSize  Insert batch count
     */
    boolean insertBatch(Collection<T> entityList, int batchSize);

    /**
     * <p>
     * Based on ID Select Modify
     * </p>
     *
     * @param entity Entity Object
     */
    boolean updateById(T entity);

    /**
     * <p>
     * Based on whereEntity Condition, update record
     * </p>
     *
     * @param entity        Entity Object
     * @param updateWrapper Entity object wrapper operation class
     *                      {@link com.baomidou.mybatisplus.core.conditions.update.UpdateWrapper}
     */
    boolean update(T entity, Wrapper<T> updateWrapper);

    /**
     * <p>
     * Based onID Batch Update
     * </p>
     *
     * @param entityList Entity object collection
     */
    boolean updateBatchById(Collection<T> entityList);

    /**
     * <p>
     * Based onID Batch Update
     * </p>
     *
     * @param entityList Entity object collection
     * @param batchSize  Update batch count
     */
    boolean updateBatchById(Collection<T> entityList, int batchSize);

    /**
     * <p>
     * Based on ID Query
     * </p>
     *
     * @param id Primary keyID
     */
    T selectById(Serializable id);

    /**
     * <p>
     * Based on ID Delete
     * </p>
     *
     * @param id Primary keyID
     */
    boolean deleteById(Serializable id);

    /**
     * <p>
     * Delete (byID Batch delete)
     * </p>
     *
     * @param idList Primary keyIDList
     */
    boolean deleteBatchIds(Collection<? extends Serializable> idList);
}