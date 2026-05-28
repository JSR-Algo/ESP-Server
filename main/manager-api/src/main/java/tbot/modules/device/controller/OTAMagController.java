package tbot.modules.device.controller;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

import org.apache.commons.lang3.StringUtils;
import org.apache.shiro.authz.annotation.RequiresPermissions;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.Parameter;
import io.swagger.v3.oas.annotations.Parameters;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import tbot.common.constant.Constant;
import tbot.common.exception.ErrorCode;
import tbot.common.page.PageData;
import tbot.common.redis.RedisKeys;
import tbot.common.redis.RedisUtils;
import tbot.common.utils.Result;
import tbot.common.validator.ValidatorUtils;
import tbot.modules.device.entity.OtaEntity;
import tbot.modules.device.service.OtaService;
import tbot.modules.security.user.SecurityUser;
import tbot.modules.sys.enums.SuperAdminEnum;
import tbot.modules.sys.service.SysParamsService;

@Tag(name = "Firmware upgrade management", description = "OTA related APIs")
@Slf4j
@RestController
@RequiredArgsConstructor
@RequestMapping("/otaMag")
public class OTAMagController {
    private static final Logger logger = LoggerFactory.getLogger(OTAController.class);
    private final OtaService otaService;
    private final RedisUtils redisUtils;
    private final SysParamsService sysParamsService;

    @GetMapping
    @Operation(summary = "Paged query OTA firmware info")
    @Parameters({
            @Parameter(name = Constant.PAGE, description = "Current page number, starts from 1", required = true),
            @Parameter(name = Constant.LIMIT, description = "Records per page", required = true)
    })
    @RequiresPermissions("sys:role:superAdmin")
    public Result<PageData<OtaEntity>> page(@Parameter(hidden = true) @RequestParam Map<String, Object> params) {
        ValidatorUtils.validateEntity(params);
        PageData<OtaEntity> page = otaService.page(params);
        return new Result<PageData<OtaEntity>>().ok(page);
    }

    @GetMapping("{id}")
    @Operation(summary = "Info OTA firmware info")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<OtaEntity> get(@PathVariable("id") String id) {
        OtaEntity data = otaService.selectById(id);
        return new Result<OtaEntity>().ok(data);
    }

    @PostMapping
    @Operation(summary = "Save OTA firmware info")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> save(@RequestBody OtaEntity entity) {
        if (entity == null) {
            return new Result<Void>().error("Firmware info cannot be empty");
        }
        if (StringUtils.isBlank(entity.getFirmwareName())) {
            return new Result<Void>().error("Firmware name cannot be empty");
        }
        if (StringUtils.isBlank(entity.getType())) {
            return new Result<Void>().error("Firmware type cannot be empty");
        }
        if (StringUtils.isBlank(entity.getVersion())) {
            return new Result<Void>().error("Version number cannot be empty");
        }
        try {
            otaService.save(entity);
            return new Result<Void>();
        } catch (RuntimeException e) {
            return new Result<Void>().error(e.getMessage());
        }
    }

    @DeleteMapping("/{id}")
    @Operation(summary = "OTA delete")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<Void> delete(@PathVariable("id") String[] ids) {
        if (ids == null || ids.length == 0) {
            return new Result<Void>().error("Deleted firmware ID cannot be empty");
        }
        otaService.delete(ids);
        return new Result<Void>();
    }

    @PutMapping("/{id}")
    @Operation(summary = "Modify OTA firmware info")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<?> update(@PathVariable("id") String id, @RequestBody OtaEntity entity) {
        if (entity == null) {
            return new Result<>().error("Firmware info cannot be empty");
        }
        entity.setId(id);
        try {
            otaService.update(entity);
            return new Result<>();
        } catch (RuntimeException e) {
            return new Result<>().error(e.getMessage());
        }
    }

    @GetMapping("/getDownloadUrl/{id}")
    @Operation(summary = "Get OTA firmware download link")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<String> getDownloadUrl(@PathVariable("id") String id) {
        String uuid = UUID.randomUUID().toString();
        redisUtils.set(RedisKeys.getOtaIdKey(uuid), id);
        return new Result<String>().ok(uuid);
    }

    @GetMapping("/download/{uuid}")
    @Operation(summary = "Download firmware file")
    public ResponseEntity<byte[]> downloadFirmware(@PathVariable("uuid") String uuid) {
        String id = (String) redisUtils.get(RedisKeys.getOtaIdKey(uuid));
        if (StringUtils.isBlank(id)) {
            return ResponseEntity.notFound().build();
        }

        // Check download count
        String downloadCountKey = RedisKeys.getOtaDownloadCountKey(uuid);
        Integer downloadCount = (Integer) Optional.ofNullable(redisUtils.get(downloadCountKey)).orElse(0);

        // If download count exceeds3times, return404
        if (downloadCount >= 3) {
            redisUtils.delete(List.of(downloadCountKey, RedisKeys.getOtaIdKey(uuid)));
            logger.warn("Download limit exceeded for UUID: {}", uuid);
            return ResponseEntity.notFound().build();
        }

        redisUtils.set(downloadCountKey, downloadCount + 1);

        try {
            // GetFirmware info
            OtaEntity otaEntity = null;
            if (id.indexOf("file:") == 0) {
                id = id.substring(5);
                otaEntity = new OtaEntity();
                otaEntity.setFirmwarePath(id);
                otaEntity.setType("assets");
                otaEntity.setVersion("1.0.0");
            } else {
                otaEntity = otaService.selectById(id);
            }

            if (otaEntity == null || StringUtils.isBlank(otaEntity.getFirmwarePath())) {
                logger.warn("Firmware not found or path is empty for ID: {}", id);
                return ResponseEntity.notFound().build();
            }

            // GetFile path - Ensure path is absolute or correct relative path
            String firmwarePath = otaEntity.getFirmwarePath();
            String originalFilename = otaEntity.getType() + "_" + otaEntity.getVersion();
            Path path;

            // Check whether absolute path
            if (Paths.get(firmwarePath).isAbsolute()) {
                path = Paths.get(firmwarePath);
            } else {
                // If relative path, resolve from current working directory
                path = Paths.get(System.getProperty("user.dir"), firmwarePath);
            }

            logger.info("Attempting to download firmware for ID: {}, DB path: {}, resolved path: {}",
                    id, firmwarePath, path.toAbsolutePath());

            if (!Files.exists(path) || !Files.isRegularFile(path)) {
                // Try directly fromfirmwareFind under directoryFilename
                String fileName = new File(firmwarePath).getName();
                Path altPath = Paths.get(System.getProperty("user.dir"), "firmware", fileName);

                logger.info("File not found at primary path, trying alternative path: {}", altPath.toAbsolutePath());

                if (Files.exists(altPath) && Files.isRegularFile(altPath)) {
                    path = altPath;
                } else {
                    logger.error("Firmware file not found at either path: {} or {}",
                            path.toAbsolutePath(), altPath.toAbsolutePath());
                    return ResponseEntity.notFound().build();
                }
            }

            // Read FileContent
            byte[] fileContent = Files.readAllBytes(path);

            // SetResponsehead

            if (firmwarePath.contains(".")) {
                String extension = firmwarePath.substring(firmwarePath.lastIndexOf("."));
                originalFilename += extension;
            }

            // CleanFilename, remove unsafe characters
            String safeFilename = originalFilename.replaceAll("[^a-zA-Z0-9._-]", "_");

            logger.info("Providing download for firmware ID: {}, filename: {}, size: {} bytes",
                    id, safeFilename, fileContent.length);

            return ResponseEntity.ok()
                    .contentType(MediaType.APPLICATION_OCTET_STREAM)
                    .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + safeFilename + "\"")
                    .body(fileContent);
        } catch (IOException e) {
            logger.error("Error reading firmware file for ID: {}", id, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        } catch (Exception e) {
            logger.error("Unexpected error during firmware download for ID: {}", id, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }

    @PostMapping("/upload")
    @Operation(summary = "Upload firmware file")
    @RequiresPermissions("sys:role:superAdmin")
    public Result<String> uploadFirmware(@RequestParam("file") MultipartFile file) {
        if (file.isEmpty()) {
            return new Result<String>().error("Upload file cannot be empty");
        }

        // CheckFile extension
        String originalFilename = file.getOriginalFilename();
        if (originalFilename == null) {
            return new Result<String>().error("File name cannot be empty");
        }

        String extension = originalFilename.substring(originalFilename.lastIndexOf(".")).toLowerCase();
        if (!extension.equals(".bin") && !extension.equals(".apk")) {
            return new Result<String>().error("Only .bin and .apk files can be uploaded");
        }

        try {
            // Validate magic bytes
            byte[] magicBytes = file.getBytes();
            if (!isValidMagicBytes(magicBytes, extension)) {
                return new Result<String>().error("Invalid file format: magic bytes do not match extension");
            }
            // Calculate file SHA-256 value
            String sha256 = calculateSHA256(file);

            // Set storage path
            String uploadDir = "uploadfile";
            Path uploadPath = Paths.get(uploadDir);

            // If directory does not exist, create directory
            if (!Files.exists(uploadPath)) {
                Files.createDirectories(uploadPath);
            }

            // Use SHA-256 as filename fixed use .bin extension
            String uniqueFileName = sha256 + extension;
            Path filePath = uploadPath.resolve(uniqueFileName);

            // Check whether file exists
            if (Files.exists(filePath)) {
                return new Result<String>().ok(filePath.toString());
            }

            // SaveFile
            Files.copy(file.getInputStream(), filePath);

            // ReturnFile path
            return new Result<String>().ok(filePath.toString());
        } catch (IOException | NoSuchAlgorithmException e) {
            return new Result<String>().error("File upload failed:" + e.getMessage());
        }
    }

    @PostMapping("/uploadAssetsBin")
    @Operation(summary = "Upload resource firmware file")
    @RequiresPermissions("sys:role:normal")
    public Result<String> uploadAssetsBin(@RequestParam("file") MultipartFile file) {
        String otaUrl = sysParamsService.getValue(Constant.SERVER_OTA, true);
        if (StringUtils.isBlank(otaUrl) || otaUrl.equals("null")) {
            return new Result<String>().error(ErrorCode.OTA_URL_EMPTY);
        }
        logger.info("username:{},uploadAssetsBin size: {}", SecurityUser.getUser().getUsername(), file.getSize());
        // VerifyFile size (Resource firmware max20MB)
        if (file.getSize() > 20 * 1024 * 1024) {
            return new Result<String>().error(ErrorCode.VOICE_CLONE_AUDIO_TOO_LARGE);
        }
        // Normal users can upload only per day50times
        if (SecurityUser.getUser().getSuperAdmin() == SuperAdminEnum.NO.value()) {
            String uploadCountKey = RedisKeys.getOtaUploadCountKey(SecurityUser.getUser().getId());
            Integer uploadCount = (Integer) Optional.ofNullable(redisUtils.get(uploadCountKey)).orElse(0);
            if (uploadCount >= 50) {
                return new Result<String>().error(ErrorCode.OTA_UPLOAD_COUNT_EXCEED);
            }
            // Increase upload count
            redisUtils.increment(RedisKeys.getOtaUploadCountKey(SecurityUser.getUser().getId()),
                    RedisUtils.DEFAULT_EXPIRE);
        }
        Result<String> result = uploadFirmware(file);

        // Generate ResourceFile path
        if (StringUtils.isNotBlank(result.getData())) {
            String uuid = UUID.randomUUID().toString();
            redisUtils.set(RedisKeys.getOtaIdKey(uuid), "file:" + result.getData());
            String downloadUrl = otaUrl.replace("/ota/", "/otaMag/download/") + uuid;
            result.setData(downloadUrl);
        }
        return result;
    }

    private String calculateSHA256(MultipartFile file) throws IOException, NoSuchAlgorithmException {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] digest = md.digest(file.getBytes());
        StringBuilder sb = new StringBuilder();
        for (byte b : digest) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private boolean isValidMagicBytes(byte[] bytes, String extension) {
        if (bytes == null || bytes.length < 4) {
            return false;
        }
        if (".bin".equals(extension)) {
            // ESP-IDF application image magic byte: 0xE9
            return bytes[0] == (byte) 0xE9;
        } else if (".apk".equals(extension)) {
            // APK / ZIP magic: PK (0x50 0x4B)
            return bytes[0] == 0x50 && bytes[1] == 0x4B;
        }
        return false;
    }
}
