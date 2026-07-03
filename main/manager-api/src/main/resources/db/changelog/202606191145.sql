SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_device' AND COLUMN_NAME = 'child_interests');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE `ai_device` ADD COLUMN `child_interests` VARCHAR(255) DEFAULT NULL COMMENT ''儿童兴趣标签'' AFTER `child_age`', 'SELECT ''Column child_interests already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_device' AND COLUMN_NAME = 'learning_style');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE `ai_device` ADD COLUMN `learning_style` VARCHAR(32) DEFAULT NULL COMMENT ''学习风格'' AFTER `child_interests`', 'SELECT ''Column learning_style already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_device' AND COLUMN_NAME = 'vocabulary_level');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE `ai_device` ADD COLUMN `vocabulary_level` VARCHAR(32) DEFAULT NULL COMMENT ''词汇水平'' AFTER `learning_style`', 'SELECT ''Column vocabulary_level already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

SET @col_exists = (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ai_device' AND COLUMN_NAME = 'parent_career');
SET @sql = IF(@col_exists = 0, 'ALTER TABLE `ai_device` ADD COLUMN `parent_career` VARCHAR(64) DEFAULT NULL COMMENT ''家长职业主题'' AFTER `vocabulary_level`', 'SELECT ''Column parent_career already exists'' AS msg');
PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
