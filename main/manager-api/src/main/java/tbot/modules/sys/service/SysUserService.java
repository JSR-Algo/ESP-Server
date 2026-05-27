package tbot.modules.sys.service;

import tbot.common.page.PageData;
import tbot.common.service.BaseService;
import tbot.modules.sys.dto.AdminPageUserDTO;
import tbot.modules.sys.dto.PasswordDTO;
import tbot.modules.sys.dto.SysUserDTO;
import tbot.modules.sys.entity.SysUserEntity;
import tbot.modules.sys.vo.AdminPageUserVO;

/**
 * System User
 */
public interface SysUserService extends BaseService<SysUserEntity> {

    SysUserDTO getByUsername(String username);

    SysUserDTO getByUserId(Long userId);

    void save(SysUserDTO dto);

    /**
     * Delete specified user, plus related data devices and agents
     * 
     * @param ids
     */
    void deleteById(Long ids);

    /**
     * Verify whether password change allowed
     * 
     * @param userId      Userid
     * @param passwordDTO Password validation params
     */
    void changePassword(Long userId, PasswordDTO passwordDTO);

    /**
     * Change password directly, no verification needed
     * 
     * @param userId   Userid
     * @param password Password
     */
    void changePasswordDirectly(Long userId, String password);

    /**
     * Reset password
     * 
     * @param userId Userid
     * @return Randomly generate compliant password
     */
    String resetPassword(Long userId);

    /**
     * Admin paged user info
     * 
     * @param dto Paged search parameters
     * @return User list paged data
     */
    PageData<AdminPageUserVO> page(AdminPageUserDTO dto);

    /**
     * Batch modify user status
     * 
     * @param status  User Status
     * @param userIds UserIDArray
     */
    void changeStatus(Integer status, String[] userIds);

    /**
     * Get whether user registration allowed
     * 
     * @return Whether user registration allowed
     */
    boolean getAllowUserRegister();
}
