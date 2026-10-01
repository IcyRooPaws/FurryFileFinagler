-- image_sorting_hashes.table_daily_totals source

CREATE OR REPLACE
ALGORITHM = UNDEFINED VIEW `image_sorting_hashes`.`table_daily_totals` AS
select
    `src`.`file_date` AS `file_date`,
    sum((case when (`src`.`table_name` = 'favorites') then `src`.`total_files` else 0 end)) AS `favorites`,
    sum((case when (`src`.`table_name` = 'manual_sort_images') then `src`.`total_files` else 0 end)) AS `manual_sort_images`,
    sum((case when (`src`.`table_name` = 'manual_sort_videos') then `src`.`total_files` else 0 end)) AS `manual_sort_videos`,
    sum((case when (`src`.`table_name` = 'sfw_image') then `src`.`total_files` else 0 end)) AS `sfw_image`,
    sum((case when (`src`.`table_name` = 'sfw_video') then `src`.`total_files` else 0 end)) AS `sfw_video`,
    sum((case when (`src`.`table_name` = 'sfw_gif') then `src`.`total_files` else 0 end)) AS `sfw_gif`,
    sum((case when (`src`.`table_name` = 'nsfw_image') then `src`.`total_files` else 0 end)) AS `nsfw_image`,
    sum((case when (`src`.`table_name` = 'nsfw_video') then `src`.`total_files` else 0 end)) AS `nsfw_video`,
    sum((case when (`src`.`table_name` = 'nsfw_gif') then `src`.`total_files` else 0 end)) AS `nsfw_gif`,
    sum((case when (`src`.`table_name` = 'other_directories') then `src`.`total_files` else 0 end)) AS `other_directories`,
    sum((case when (`src`.`table_name` = 'archived_favorites') then `src`.`total_files` else 0 end)) AS `archived_favorites`
from
    (
    select
        'favorites' AS `table_name`,
        cast(`image_sorting_hashes`.`favorites`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`favorites`
    group by
        cast(`image_sorting_hashes`.`favorites`.`file_modified_at` as date)
union all
    select
        'manual_sort_images' AS `table_name`,
        cast(`image_sorting_hashes`.`manual_sort_images`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`manual_sort_images`
    group by
        cast(`image_sorting_hashes`.`manual_sort_images`.`file_modified_at` as date)
union all
    select
        'manual_sort_videos' AS `table_name`,
        cast(`image_sorting_hashes`.`manual_sort_videos`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`manual_sort_videos`
    group by
        cast(`image_sorting_hashes`.`manual_sort_videos`.`file_modified_at` as date)
union all
    select
        'sfw_image' AS `table_name`,
        cast(`image_sorting_hashes`.`sfw_image`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`sfw_image`
    group by
        cast(`image_sorting_hashes`.`sfw_image`.`file_modified_at` as date)
union all
    select
        'sfw_video' AS `table_name`,
        cast(`image_sorting_hashes`.`sfw_video`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`sfw_video`
    group by
        cast(`image_sorting_hashes`.`sfw_video`.`file_modified_at` as date)
union all
    select
        'sfw_gif' AS `table_name`,
        cast(`image_sorting_hashes`.`sfw_gif`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`sfw_gif`
    group by
        cast(`image_sorting_hashes`.`sfw_gif`.`file_modified_at` as date)
union all
    select
        'nsfw_image' AS `table_name`,
        cast(`image_sorting_hashes`.`nsfw_image`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`nsfw_image`
    group by
        cast(`image_sorting_hashes`.`nsfw_image`.`file_modified_at` as date)
union all
    select
        'nsfw_video' AS `table_name`,
        cast(`image_sorting_hashes`.`nsfw_video`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`nsfw_video`
    group by
        cast(`image_sorting_hashes`.`nsfw_video`.`file_modified_at` as date)
union all
    select
        'nsfw_gif' AS `table_name`,
        cast(`image_sorting_hashes`.`nsfw_gif`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`nsfw_gif`
    group by
        cast(`image_sorting_hashes`.`nsfw_gif`.`file_modified_at` as date)
union all
    select
        'other_directories' AS `table_name`,
        cast(`image_sorting_hashes`.`other_directories`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`other_directories`
    group by
        cast(`image_sorting_hashes`.`other_directories`.`file_modified_at` as date)
union all
    select
        'archived_favorites' AS `table_name`,
        cast(`image_sorting_hashes`.`archived_favorites`.`file_modified_at` as date) AS `file_date`,
        count(0) AS `total_files`
    from
        `image_sorting_hashes`.`archived_favorites`
    group by
        cast(`image_sorting_hashes`.`archived_favorites`.`file_modified_at` as date)) `src`
group by
    `src`.`file_date`
order by
    `src`.`file_date`;