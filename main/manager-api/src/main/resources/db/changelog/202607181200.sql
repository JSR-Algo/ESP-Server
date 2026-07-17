ALTER TABLE `ai_device`
    ADD COLUMN `child_interests_json` TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL
        COMMENT 'Canonical child interests JSON managed by profile projection'
        AFTER `child_interests`;

WITH `projection_state` AS (
    SELECT `id`,
           (`child_profile_id` IS NOT NULL
            AND `child_interests` IS NOT NULL
            AND JSON_VALID(`child_interests`)
            AND `child_interests` REGEXP '^[[:space:]]*\\['
            AND NOT EXISTS (
                SELECT 1
                  FROM JSON_TABLE(
                      IF(JSON_VALID(`child_interests`), `child_interests`, JSON_ARRAY(JSON_OBJECT())),
                      '$[*]' COLUMNS (`interest` JSON PATH '$')
                  ) AS `decoded_interests`
                 WHERE JSON_TYPE(`decoded_interests`.`interest`) <> 'STRING'
            )) AS `preserve_replace`,
           (`child_profile_id` IS NULL
            AND `child_birth_year` IS NULL
            AND `child_name` IS NULL
            AND `child_age` IS NULL
            AND `child_interests` IS NULL
            AND `learning_style` IS NULL
            AND `vocabulary_level` IS NULL
            AND `parent_career` IS NULL) AS `preserve_clear`
      FROM `ai_device`
     WHERE `child_profile_revision` >= 0
)
UPDATE `ai_device` AS `device`
JOIN `projection_state` AS `state` ON `state`.`id` = `device`.`id`
   SET `device`.`child_profile_id` = IF(`state`.`preserve_replace`, `device`.`child_profile_id`, NULL),
       `device`.`child_birth_year` = IF(`state`.`preserve_replace`, `device`.`child_birth_year`, NULL),
       `device`.`child_name` = IF(`state`.`preserve_replace`, `device`.`child_name`, NULL),
       `device`.`child_age` = NULL,
       `device`.`child_interests_json` = IF(`state`.`preserve_replace`, `device`.`child_interests`, NULL),
       `device`.`child_interests` = NULL,
       `device`.`learning_style` = IF(`state`.`preserve_replace`, `device`.`learning_style`, NULL),
       `device`.`vocabulary_level` = IF(`state`.`preserve_replace`, `device`.`vocabulary_level`, NULL),
       `device`.`parent_career` = IF(`state`.`preserve_replace`, `device`.`parent_career`, NULL),
       `device`.`child_profile_revision` = IF(
           `state`.`preserve_replace` OR `state`.`preserve_clear`,
           `device`.`child_profile_revision`,
           -1),
       `device`.`child_profile_payload_hash` = IF(
           `state`.`preserve_replace` OR `state`.`preserve_clear`,
           `device`.`child_profile_payload_hash`,
           NULL);
