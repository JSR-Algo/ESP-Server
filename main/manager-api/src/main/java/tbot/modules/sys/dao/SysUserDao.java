package tbot.modules.sys.dao;

import org.apache.ibatis.annotations.Mapper;

import tbot.common.dao.BaseDao;
import tbot.modules.sys.entity.SysUserEntity;

/**
 * System User
 */
@Mapper
public interface SysUserDao extends BaseDao<SysUserEntity> {

}