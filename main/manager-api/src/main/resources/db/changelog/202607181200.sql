ALTER TABLE `ai_device`
    ADD COLUMN `child_interests_json` TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL
        COMMENT 'Canonical child interests JSON managed by profile projection'
        AFTER `child_interests`;

WITH RECURSIVE `legacy_interest_parts` AS (
    SELECT `id`,
           IF(LOCATE(',', `child_interests`) = 0,
              NULL,
              SUBSTRING(`child_interests`, LOCATE(',', `child_interests`) + 1)) AS `remainder`,
           CAST(CONCAT('[', JSON_QUOTE(SUBSTRING_INDEX(`child_interests`, ',', 1))) AS CHAR(4096))
               AS `encoded`
      FROM `ai_device`
     WHERE `child_profile_revision` >= 0
       AND `child_profile_id` IS NOT NULL
       AND `child_interests` IS NOT NULL
       AND `child_interests` <> ''
    UNION ALL
    SELECT `id`,
           IF(LOCATE(',', `remainder`) = 0,
              NULL,
              SUBSTRING(`remainder`, LOCATE(',', `remainder`) + 1)) AS `remainder`,
           CONCAT(`encoded`, ',', JSON_QUOTE(SUBSTRING_INDEX(`remainder`, ',', 1))) AS `encoded`
      FROM `legacy_interest_parts`
     WHERE `remainder` IS NOT NULL
),
`legacy_interests_json` AS (
    SELECT `id`, CONCAT(`encoded`, ']') AS `encoded`
      FROM `legacy_interest_parts`
     WHERE `remainder` IS NULL
)
UPDATE `ai_device` AS `device`
LEFT JOIN `legacy_interests_json` AS `legacy` ON `legacy`.`id` = `device`.`id`
   SET `device`.`child_interests_json` = CASE
           WHEN `device`.`child_profile_id` IS NULL THEN NULL
           WHEN `device`.`child_interests` IS NULL OR `device`.`child_interests` = '' THEN JSON_ARRAY()
           ELSE `legacy`.`encoded`
       END,
       `device`.`child_interests` = NULL,
       `device`.`child_age` = NULL
 WHERE `device`.`child_profile_revision` >= 0;
