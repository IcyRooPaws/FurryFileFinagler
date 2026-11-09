-- e621_posts_db: schema snapshot written by 'fff db snapshot'. Reference only;
-- 'fff db init' builds the tables FFF uses from the code.

-- table: ai_flagged_artists
CREATE TABLE `ai_flagged_artists` (
  `artist_flag_id` int NOT NULL AUTO_INCREMENT,
  `artist_id` int DEFAULT NULL,
  `artist_name` varchar(100) NOT NULL,
  `first_flagged_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `last_flagged_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `flag_count` int NOT NULL DEFAULT '1',
  PRIMARY KEY (`artist_flag_id`),
  UNIQUE KEY `artist_name` (`artist_name`),
  KEY `idx_ai_flagged_artist_id` (`artist_id`),
  KEY `idx_ai_flagged_last_seen` (`last_flagged_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: ai_generated_posts_archive
CREATE TABLE `ai_generated_posts_archive` (
  `archive_id` int NOT NULL AUTO_INCREMENT,
  `post_id` int NOT NULL,
  `file_name` varchar(300) DEFAULT NULL,
  `file_path` varchar(255) DEFAULT NULL,
  `rating` varchar(20) DEFAULT NULL,
  `deletion_reason` text,
  `deleted_at` datetime DEFAULT NULL,
  `ingested_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `file_exists` tinyint(1) DEFAULT '0',
  `archived_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `artist_id` int DEFAULT NULL,
  `snapshot_json` longtext,
  PRIMARY KEY (`archive_id`),
  UNIQUE KEY `post_id` (`post_id`),
  KEY `idx_ai_archived_at` (`archived_at`),
  KEY `idx_ai_deletion_reason` (`deletion_reason`(255)),
  KEY `idx_ai_artist_id` (`artist_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: artist_file_renames
CREATE TABLE `artist_file_renames` (
  `rename_id` int NOT NULL AUTO_INCREMENT,
  `post_id` int NOT NULL,
  `old_file_name` varchar(300) DEFAULT NULL,
  `new_file_name` varchar(300) DEFAULT NULL,
  `old_file_path` varchar(255) DEFAULT NULL,
  `new_file_path` varchar(255) DEFAULT NULL,
  `old_artist_names` text,
  `new_artist_names` text,
  `renamed_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`rename_id`),
  KEY `idx_post_id` (`post_id`),
  KEY `idx_renamed_at` (`renamed_at`),
  CONSTRAINT `artist_file_renames_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: artists
CREATE TABLE `artists` (
  `artist_id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`artist_id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: e621_perceptual_hashes
CREATE TABLE `e621_perceptual_hashes` (
  `post_id` int NOT NULL,
  `perceptual_hash` text,
  `hash_algorithm` varchar(64) DEFAULT 'average',
  `file_url` varchar(255) DEFAULT NULL,
  `cached_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`post_id`),
  KEY `idx_perceptual_hash` (`perceptual_hash`(255)),
  CONSTRAINT `e621_perceptual_hashes_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: fa_pending_unfavorites
CREATE TABLE `fa_pending_unfavorites` (
  `fa_post_id` varchar(20) NOT NULL,
  `attempts` int NOT NULL DEFAULT '0',
  `last_error` varchar(255) DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `last_attempt_at` datetime DEFAULT NULL,
  PRIMARY KEY (`fa_post_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: pools
CREATE TABLE `pools` (
  `pool_id` int NOT NULL,
  `name` varchar(255) DEFAULT NULL,
  `description` text,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `post_count` int DEFAULT '0',
  `is_active` tinyint(1) DEFAULT '1',
  `category` varchar(50) DEFAULT NULL,
  `ingested_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `pool_url` varchar(255) GENERATED ALWAYS AS (concat(_utf8mb4'https://e621.net/pools/',`pool_id`)) STORED,
  PRIMARY KEY (`pool_id`),
  KEY `idx_name` (`name`),
  KEY `idx_category` (`category`),
  KEY `idx_is_active` (`is_active`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: post_relationships
CREATE TABLE `post_relationships` (
  `id` int NOT NULL AUTO_INCREMENT,
  `post_id` int NOT NULL,
  `parent_id` int DEFAULT NULL,
  `child_id` int DEFAULT NULL,
  `created_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_relationship` (`post_id`,`parent_id`,`child_id`),
  KEY `idx_post_id` (`post_id`),
  KEY `idx_parent_id` (`parent_id`),
  KEY `idx_child_id` (`child_id`),
  CONSTRAINT `post_relationships_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: post_takedowns
CREATE TABLE `post_takedowns` (
  `post_id` int NOT NULL,
  `takedown_id` int NOT NULL,
  `linked_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`post_id`,`takedown_id`),
  KEY `idx_post_id` (`post_id`),
  KEY `idx_takedown_id` (`takedown_id`),
  CONSTRAINT `post_takedowns_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `post_takedowns_ibfk_2` FOREIGN KEY (`takedown_id`) REFERENCES `takedowns` (`takedown_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: posts
CREATE TABLE `posts` (
  `id` int NOT NULL,
  `file_url` varchar(255) DEFAULT NULL,
  `md5` varchar(32) DEFAULT NULL,
  `media_hash` text,
  `rating` varchar(20) DEFAULT NULL,
  `ingested_at` datetime DEFAULT NULL,
  `file_path` varchar(255) DEFAULT NULL,
  `file_type` varchar(20) DEFAULT NULL,
  `file_name` varchar(300) DEFAULT NULL,
  `deleted` tinyint(1) DEFAULT '0',
  `deleted_at` datetime DEFAULT NULL,
  `deletion_reason` text,
  `lastChecked` datetime DEFAULT NULL,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: posts_artists
CREATE TABLE `posts_artists` (
  `post_id` int NOT NULL,
  `artist_id` int NOT NULL,
  PRIMARY KEY (`post_id`,`artist_id`),
  KEY `artist_id` (`artist_id`),
  CONSTRAINT `posts_artists_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `posts_artists_ibfk_2` FOREIGN KEY (`artist_id`) REFERENCES `artists` (`artist_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: posts_pools
CREATE TABLE `posts_pools` (
  `post_id` int NOT NULL,
  `pool_id` int NOT NULL,
  PRIMARY KEY (`post_id`,`pool_id`),
  KEY `pool_id` (`pool_id`),
  CONSTRAINT `posts_pools_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `posts_pools_ibfk_2` FOREIGN KEY (`pool_id`) REFERENCES `pools` (`pool_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: posts_tags
CREATE TABLE `posts_tags` (
  `post_id` int NOT NULL,
  `tag_id` int NOT NULL,
  PRIMARY KEY (`post_id`,`tag_id`),
  KEY `tag_id` (`tag_id`),
  CONSTRAINT `posts_tags_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE,
  CONSTRAINT `posts_tags_ibfk_2` FOREIGN KEY (`tag_id`) REFERENCES `tags` (`tag_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: source_links
CREATE TABLE `source_links` (
  `post_id` int NOT NULL,
  `source_link` varchar(1000) NOT NULL,
  PRIMARY KEY (`post_id`,`source_link`(255)),
  CONSTRAINT `source_links_ibfk_1` FOREIGN KEY (`post_id`) REFERENCES `posts` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: tags
CREATE TABLE `tags` (
  `tag_id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) DEFAULT NULL,
  `tag_type` varchar(50) DEFAULT NULL,
  PRIMARY KEY (`tag_id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: takedowns
CREATE TABLE `takedowns` (
  `takedown_id` int NOT NULL,
  `reason` text,
  `instructions` text,
  `admin_notes` text,
  `created_at` datetime DEFAULT NULL,
  `updated_at` datetime DEFAULT NULL,
  `finalized_at` datetime DEFAULT NULL,
  `status` varchar(50) DEFAULT NULL,
  `scraped_at` datetime DEFAULT CURRENT_TIMESTAMP,
  `last_updated` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `raw_html_snippet` text,
  PRIMARY KEY (`takedown_id`),
  KEY `idx_status` (`status`),
  KEY `idx_finalized_at` (`finalized_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- table: unfavorited_posts_archive
CREATE TABLE `unfavorited_posts_archive` (
  `archive_id` int NOT NULL AUTO_INCREMENT,
  `post_id` int NOT NULL,
  `file_name` varchar(300) DEFAULT NULL,
  `rating` varchar(20) DEFAULT NULL,
  `ingested_at` datetime DEFAULT NULL,
  `file_path` varchar(255) DEFAULT NULL,
  `had_file` tinyint(1) DEFAULT '0',
  `archived_at` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`archive_id`),
  KEY `idx_post_id` (`post_id`),
  KEY `idx_archived_at` (`archived_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- view: v_top_10_characters
CREATE ALGORITHM=UNDEFINED SQL SECURITY DEFINER VIEW `e621_posts_db`.`v_top_10_characters` AS with `character_base` as (select `t`.`name` AS `name`,count(`pt`.`post_id`) AS `tag_count` from ((`e621_posts_db`.`tags` `t` join `e621_posts_db`.`posts_tags` `pt` on((`t`.`tag_id` = `pt`.`tag_id`))) join `e621_posts_db`.`posts` `p` on((`pt`.`post_id` = `p`.`id`))) where ((`t`.`tag_type` = 'Character') and (`t`.`name` <> 'fan_character')) group by `t`.`name` order by `tag_count` desc limit 9), `species_yoshi` as (select `t`.`name` AS `name`,count(`pt`.`post_id`) AS `tag_count` from ((`e621_posts_db`.`tags` `t` join `e621_posts_db`.`posts_tags` `pt` on((`t`.`tag_id` = `pt`.`tag_id`))) join `e621_posts_db`.`posts` `p` on((`pt`.`post_id` = `p`.`id`))) where ((`t`.`tag_type` = 'Species') and (`t`.`name` like '%yoshi%')) group by `t`.`name`), `combined_raw` as (select `u`.`name` AS `name`,sum(`u`.`tag_count`) AS `total_count` from (select `character_base`.`name` AS `name`,`character_base`.`tag_count` AS `tag_count` from `character_base` union all select `species_yoshi`.`name` AS `name`,`species_yoshi`.`tag_count` AS `tag_count` from `species_yoshi`) `u` group by `u`.`name`), `top_10` as (select `combined_raw`.`name` AS `character_name`,`combined_raw`.`total_count` AS `total_count` from `combined_raw` order by `combined_raw`.`total_count` desc,`combined_raw`.`name` limit 10) select `top_10`.`character_name` AS `character_name`,`top_10`.`total_count` AS `total_count` from `top_10` order by `top_10`.`total_count` desc,`top_10`.`character_name`;

-- procedure: GetTop10Artists
CREATE PROCEDURE `GetTop10Artists`()
BEGIN
  SET @sql = NULL;
  SELECT GROUP_CONCAT(
           CONCAT(
             'MAX(CASE WHEN name = ''',
             name,
             ''' THEN tag_count END) AS `',
             name,
             '`'
           )
         ) INTO @sql
  FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN ('sound_warning', 'third-party_edit', 'conditional_dnp','avoid_posting')
    GROUP BY t.name
    ORDER BY tag_count DESC
    LIMIT 10
  ) AS sub;
  SET @query = CONCAT('SELECT ', @sql, ' FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN (''sound_warning'', ''third-party_edit'')
    GROUP BY t.name
  ) AS subq;');
  PREPARE stmt FROM @query;
  EXECUTE stmt;
  DEALLOCATE PREPARE stmt;
END;

-- procedure: GetTop10Characters
CREATE PROCEDURE `GetTop10Characters`()
proc:BEGIN
  SET @old_gcml := @@SESSION.group_concat_max_len;
  SET SESSION group_concat_max_len = 1048576;
  SET @sql := NULL;
  SELECT GROUP_CONCAT(
           CONCAT(
             'COALESCE(MAX(CASE WHEN name = ''',
             REPLACE(name, '''', ''''''),
             ''' THEN total_count END),0) AS `',
             REPLACE(name, '`', '``'),
             '`'
           )
           ORDER BY total_count DESC, name
           SEPARATOR ','
         )
    INTO @sql
  FROM (
    SELECT name, SUM(tag_count) AS total_count
    FROM (
      SELECT name, tag_count
      FROM (
        SELECT t.name, COUNT(pt.post_id) AS tag_count
        FROM tags t
        JOIN posts_tags pt ON t.tag_id = pt.tag_id
        JOIN posts p ON pt.post_id = p.id
        WHERE t.tag_type = 'Character'
          AND t.name <> 'fan_character'
        GROUP BY t.name
        ORDER BY tag_count DESC
        LIMIT 9
      ) AS top_chars
      UNION ALL
      SELECT t.name, COUNT(pt.post_id) AS tag_count
      FROM tags t
      JOIN posts_tags pt ON t.tag_id = pt.tag_id
      JOIN posts p ON pt.post_id = p.id
      WHERE t.tag_type = 'Species'
        AND t.name LIKE 'yoshi'
      GROUP BY t.name
    ) AS combined_raw
    GROUP BY name
  ) AS totals;
  IF @sql IS NULL OR @sql = '' THEN
    SELECT 'No matching tags found' AS message;
    SET SESSION group_concat_max_len = @old_gcml;
    LEAVE proc;
  END IF;
  SET @query := CONCAT(
    'SELECT ', @sql, ' FROM (',
    '  SELECT name, SUM(tag_count) AS total_count',
    '  FROM (',
    '    SELECT name, tag_count FROM (',
    '      SELECT t.name, COUNT(pt.post_id) AS tag_count',
    '      FROM tags t',
    '      JOIN posts_tags pt ON t.tag_id = pt.tag_id',
    '      JOIN posts p ON pt.post_id = p.id',
    '      WHERE t.tag_type = ''Character''',
    '        AND t.name <> ''fan_character''',
    '      GROUP BY t.name',
    '      ORDER BY tag_count DESC',
    '      LIMIT 10',
    '    ) AS top_chars',
    '    UNION ALL',
    '    SELECT t.name, COUNT(pt.post_id) AS tag_count',
    '    FROM tags t',
    '    JOIN posts_tags pt ON t.tag_id = pt.tag_id',
    '    JOIN posts p ON pt.post_id = p.id',
    '    WHERE t.tag_type = ''Species''',
    '      AND t.name LIKE ''yoshi''',
    '    GROUP BY t.name',
    '  ) AS combined_raw',
    '  GROUP BY name',
    ') AS totals;'
  );
  PREPARE stmt FROM @query;
  EXECUTE stmt;
  DEALLOCATE PREPARE stmt;
  SET SESSION group_concat_max_len = @old_gcml;
END proc;

-- procedure: GetTop20Artists
CREATE PROCEDURE `GetTop20Artists`()
BEGIN
  SET @sql = NULL;
  SELECT GROUP_CONCAT(
           CONCAT(
             'MAX(CASE WHEN name = ''',
             name,
             ''' THEN tag_count END) AS `',
             name,
             '`'
           )
         ) INTO @sql
  FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN ('sound_warning', 'third-party_edit')
    GROUP BY t.name
    ORDER BY tag_count DESC
    LIMIT 20
  ) AS sub;
  SET @query = CONCAT('SELECT ', @sql, ' FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN (''sound_warning'', ''third-party_edit'')
    GROUP BY t.name
  ) AS subq;');
  PREPARE stmt FROM @query;
  EXECUTE stmt;
  DEALLOCATE PREPARE stmt;
END;

-- procedure: GetTop20Characters
CREATE PROCEDURE `GetTop20Characters`()
BEGIN
  SET @sql = NULL;
  SELECT GROUP_CONCAT(
           CONCAT(
             'MAX(CASE WHEN name = ''',
             name,
             ''' THEN tag_count END) AS `',
             name,
             '`'
           )
         ) INTO @sql
  FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM tags t
    JOIN posts_tags pt ON t.tag_id = pt.tag_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.tag_type = 'Character'
      AND t.name != 'fan_character'
    GROUP BY t.name
    ORDER BY tag_count DESC
    LIMIT 20
  ) AS sub;
  SET @query = CONCAT('SELECT ', @sql, ' FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM tags t
    JOIN posts_tags pt ON t.tag_id = pt.tag_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.tag_type = ''Character'' AND t.name != ''fan_character''
    GROUP BY t.name
  ) AS subq;');
  PREPARE stmt FROM @query;
  EXECUTE stmt;
  DEALLOCATE PREPARE stmt;
END;

-- procedure: GetTop25Artists
CREATE PROCEDURE `GetTop25Artists`()
BEGIN
  SET @sql = NULL;
  SELECT GROUP_CONCAT(
           CONCAT(
             'MAX(CASE WHEN name = ''',
             name,
             ''' THEN tag_count END) AS `',
             name,
             '`'
           )
         ) INTO @sql
  FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN ('sound_warning', 'third-party_edit')
    GROUP BY t.name
    ORDER BY tag_count DESC
    LIMIT 25
  ) AS sub;
  SET @query = CONCAT('SELECT ', @sql, ' FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN (''sound_warning'', ''third-party_edit'')
    GROUP BY t.name
  ) AS subq;');
  PREPARE stmt FROM @query;
  EXECUTE stmt;
  DEALLOCATE PREPARE stmt;
END;

-- procedure: GetTop50Artists
CREATE PROCEDURE `GetTop50Artists`()
BEGIN
  SET @sql = NULL;
  SELECT GROUP_CONCAT(
           CONCAT(
             'MAX(CASE WHEN name = ''',
             name,
             ''' THEN tag_count END) AS `',
             name,
             '`'
           )
         ) INTO @sql
  FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN ('sound_warning', 'third-party_edit')
    GROUP BY t.name
    ORDER BY tag_count DESC
    LIMIT 50
  ) AS sub;
  SET @query = CONCAT('SELECT ', @sql, ' FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM artists t
    JOIN posts_artists pt ON t.artist_id = pt.artist_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.name NOT IN (''sound_warning'', ''third-party_edit'')
    GROUP BY t.name
  ) AS subq;');
  PREPARE stmt FROM @query;
  EXECUTE stmt;
  DEALLOCATE PREPARE stmt;
END;

-- procedure: GetTop50Characters
CREATE PROCEDURE `GetTop50Characters`()
BEGIN
  SET @sql = NULL;
  SELECT GROUP_CONCAT(
           CONCAT(
             'MAX(CASE WHEN name = ''',
             REPLACE(name, '''', ''''''),
             ''' THEN tag_count END) AS `',
             name,
             '`'
           )
         ) INTO @sql
  FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM tags t
    JOIN posts_tags pt ON t.tag_id = pt.tag_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.tag_type = 'Character'
      AND t.name != 'fan_character'
    GROUP BY t.name
    ORDER BY tag_count DESC
    LIMIT 50
  ) AS sub;
  SET @query = CONCAT('SELECT ', @sql, ' FROM (
    SELECT t.name, COUNT(pt.post_id) AS tag_count
    FROM tags t
    JOIN posts_tags pt ON t.tag_id = pt.tag_id
    JOIN posts p ON pt.post_id = p.id
    WHERE t.tag_type = ''Character'' AND t.name != ''fan_character''
    GROUP BY t.name
  ) AS subq;');
  PREPARE stmt FROM @query;
  EXECUTE stmt;
  DEALLOCATE PREPARE stmt;
END;
