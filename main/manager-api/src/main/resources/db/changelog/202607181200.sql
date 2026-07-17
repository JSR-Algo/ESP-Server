ALTER TABLE `ai_device`
    ADD COLUMN `child_interests_json` TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL
        COMMENT 'Canonical child interests JSON managed by profile projection'
        AFTER `child_interests`;
