SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_device' AND COLUMN_NAME = 'child_name');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE `ai_device` ADD COLUMN `child_name` VARCHAR(64) DEFAULT NULL COMMENT ''儿童称呼'' AFTER `alias`', 'SELECT ''Column child_name already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_device' AND COLUMN_NAME = 'child_age');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE `ai_device` ADD COLUMN `child_age` TINYINT UNSIGNED DEFAULT NULL COMMENT ''儿童年龄'' AFTER `child_name`', 'SELECT ''Column child_age already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
