package tbot.modules.sys.service.impl;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import org.apache.commons.lang3.StringUtils;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import com.baomidou.mybatisplus.core.conditions.query.QueryWrapper;
import com.baomidou.mybatisplus.core.metadata.IPage;

import lombok.AllArgsConstructor;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.exception.RenException;
import tbot.common.page.PageData;
import tbot.common.service.impl.BaseServiceImpl;
import tbot.common.utils.ConvertUtils;
import tbot.modules.agent.service.AgentService;
import tbot.modules.device.service.DeviceService;
import tbot.modules.security.password.PasswordUtils;
import tbot.modules.sys.dao.SysUserDao;
import tbot.modules.sys.dto.AdminPageUserDTO;
import tbot.modules.sys.dto.PasswordDTO;
import tbot.modules.sys.dto.SysUserDTO;
import tbot.modules.sys.entity.SysUserEntity;
import tbot.modules.sys.enums.SuperAdminEnum;
import tbot.modules.sys.service.SysParamsService;
import tbot.modules.sys.service.SysUserService;
import tbot.modules.sys.vo.AdminPageUserVO;

/**
 * System User
 */
@AllArgsConstructor
@Service
public class SysUserServiceImpl extends BaseServiceImpl<SysUserDao, SysUserEntity> implements SysUserService {
    private final SysUserDao sysUserDao;

    private final DeviceService deviceService;

    private final AgentService agentService;

    private final SysParamsService sysParamsService;

    @Override
    public SysUserDTO getByUsername(String username) {
        QueryWrapper<SysUserEntity> queryWrapper = new QueryWrapper<>();
        queryWrapper.eq("username", username);
        List<SysUserEntity> users = sysUserDao.selectList(queryWrapper);
        if (users == null || users.isEmpty()) {
            return null;
        }
        SysUserEntity entity = users.get(0);
        return ConvertUtils.sourceToTarget(entity, SysUserDTO.class);
    }

    @Override
    public SysUserDTO getByUserId(Long userId) {
        SysUserEntity sysUserEntity = sysUserDao.selectById(userId);

        return ConvertUtils.sourceToTarget(sysUserEntity, SysUserDTO.class);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void save(SysUserDTO dto) {
        SysUserEntity entity = ConvertUtils.sourceToTarget(dto, SysUserEntity.class);

        // Password Strength
        if (!isStrongPassword(entity.getPassword())) {
            throw new RenException(ErrorCode.PASSWORD_WEAK_ERROR);
        }

        // Password Encryption
        String password = PasswordUtils.encode(entity.getPassword());
        entity.setPassword(password);

        // Save User
        Long userCount = getUserCount();
        if (userCount == 0) {
            entity.setSuperAdmin(SuperAdminEnum.YES.value());
        } else {
            entity.setSuperAdmin(SuperAdminEnum.NO.value());
        }
        entity.setStatus(1);

        insert(entity);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void deleteById(Long id) {
        // Delete user
        baseDao.deleteById(id);
        // Delete device
        deviceService.deleteByUserId(id);
        // Delete agent
        agentService.deleteAgentByUserId(id);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void changePassword(Long userId, PasswordDTO passwordDTO) {
        SysUserEntity sysUserEntity = sysUserDao.selectById(userId);

        if (null == sysUserEntity) {
            throw new RenException(ErrorCode.TOKEN_INVALID);
        }

        // Check old password correct
        if (!PasswordUtils.matches(passwordDTO.getPassword(), sysUserEntity.getPassword())) {
            throw new RenException(ErrorCode.OLD_PASSWORD_ERROR);
        }

        // New password strength
        if (!isStrongPassword(passwordDTO.getNewPassword())) {
            throw new RenException(ErrorCode.PASSWORD_WEAK_ERROR);
        }

        // Password Encryption
        String password = PasswordUtils.encode(passwordDTO.getNewPassword());
        sysUserEntity.setPassword(password);

        updateById(sysUserEntity);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void changePasswordDirectly(Long userId, String password) {
        // New password strength
        if (!isStrongPassword(password)) {
            throw new RenException(ErrorCode.PASSWORD_WEAK_ERROR);
        }
        SysUserEntity sysUserEntity = new SysUserEntity();
        sysUserEntity.setId(userId);
        sysUserEntity.setPassword(PasswordUtils.encode(password));
        updateById(sysUserEntity);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public String resetPassword(Long userId) {
        String password = generatePassword();
        changePasswordDirectly(userId, password);
        return password;
    }

    private Long getUserCount() {
        QueryWrapper<SysUserEntity> queryWrapper = new QueryWrapper<>();
        return baseDao.selectCount(queryWrapper);
    }

    @Override
    public PageData<AdminPageUserVO> page(AdminPageUserDTO dto) {
        Map<String, Object> params = new HashMap<String, Object>();
        params.put(Constant.PAGE, dto.getPage());
        params.put(Constant.LIMIT, dto.getLimit());
        IPage<SysUserEntity> page = baseDao.selectPage(
                getPage(params, "id", true),
                new QueryWrapper<SysUserEntity>().like(StringUtils.isNotBlank(dto.getMobile()), "username",
                        dto.getMobile()));
        // Loop ProcessingpageGot data, return needed fields
        List<AdminPageUserVO> list = page.getRecords().stream().map(user -> {
            AdminPageUserVO adminPageUserVO = new AdminPageUserVO();
            adminPageUserVO.setUserid(user.getId().toString());
            adminPageUserVO.setMobile(user.getUsername());
            String deviceCount = deviceService.selectCountByUserId(user.getId()).toString();
            adminPageUserVO.setDeviceCount(deviceCount);
            adminPageUserVO.setStatus(user.getStatus());
            adminPageUserVO.setCreateDate(user.getCreateDate());
            return adminPageUserVO;
        }).toList();
        return new PageData<>(list, page.getTotal());
    }

    private boolean isStrongPassword(String password) {
        // Weak password regex
        String weakPasswordRegex = "^(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z]).+$";
        Pattern pattern = Pattern.compile(weakPasswordRegex);
        Matcher matcher = pattern.matcher(password);
        return matcher.matches();
    }

    private static final String CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()";
    private static final Random random = new Random();

    /**
     * Generate random password
     * 
     * @return Random generated password
     */
    private String generatePassword() {
        StringBuilder password = new StringBuilder();

        // Ensure contains at least one digit
        password.append("0123456789".charAt(random.nextInt(10)));
        // Ensure contains at least one lowercase letter
        password.append("abcdefghijklmnopqrstuvwxyz".charAt(random.nextInt(26)));
        // Ensure contains at least one uppercase letter
        password.append("ABCDEFGHIJKLMNOPQRSTUVWXYZ".charAt(random.nextInt(26)));
        // Ensure contains at least one special symbol
        password.append("!@#$%^&*()".charAt(random.nextInt(10)));

        // Generate remaining8Characters
        for (int i = 4; i < 12; i++) {
            password.append(CHARACTERS.charAt(random.nextInt(CHARACTERS.length())));
        }

        // Shuffle character order in password
        char[] passwordChars = password.toString().toCharArray();
        for (int i = 0; i < passwordChars.length; i++) {
            int randomIndex = random.nextInt(passwordChars.length);
            char temp = passwordChars[i];
            passwordChars[i] = passwordChars[randomIndex];
            passwordChars[randomIndex] = temp;
        }

        return new String(passwordChars);
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public void changeStatus(Integer status, String[] userIds) {
        for (String userId : userIds) {
            SysUserEntity entity = new SysUserEntity();
            entity.setId(Long.parseLong(userId));
            entity.setStatus(status);
            updateById(entity);
        }
    }

    @Override
    public boolean getAllowUserRegister() {
        String allowUserRegister = sysParamsService.getValue(Constant.SERVER_ALLOW_USER_REGISTER, true);
        if (allowUserRegister.equals("true")) {
            return true;
        }
        Long userCount = baseDao.selectCount(new QueryWrapper<SysUserEntity>());
        if (userCount == 0) {
            return true;
        }
        return false;
    }
}
