ALTER TABLE `ai_device`
    ADD COLUMN `child_profile_id` CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER `parent_career`,
    ADD COLUMN `child_birth_year` INT NULL AFTER `child_profile_id`,
    ADD COLUMN `child_profile_revision` BIGINT NOT NULL DEFAULT -1 AFTER `child_birth_year`,
    ADD COLUMN `child_profile_payload_hash` VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL AFTER `child_profile_revision`;
